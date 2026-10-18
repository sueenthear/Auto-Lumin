from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from typing import Callable


def configure_logging() -> logging.Logger:
    log_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "OkLuminTool",
        "logs",
    )
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("ok_lumin_tool")
    if not logger.handlers:
        handler = RotatingFileHandler(
            os.path.join(log_dir, "ok-lumin-tool.log"),
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


logger = configure_logging()


def ensure_admin() -> None:
    if os.name != "nt" or ctypes.windll.shell32.IsUserAnAdmin():
        return
    executable = sys.executable
    if getattr(sys, "frozen", False):
        arguments = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    else:
        arguments = '-m "dev_tool.ok_lumin_tool"'
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, arguments, os.getcwd(), 1)
    if result <= 32:
        raise OSError(f"Could not restart as administrator (ShellExecuteW error {result})")
    raise SystemExit(0)


class GlobalShortcut:
    """Register global F8/F9/F10 actions while the application is running."""

    MOD_NOREPEAT = 0x4000
    HOTKEYS = {0x77: 1, 0x78: 2, 0x79: 3}
    WM_HOTKEY = 0x0312

    def __init__(self, callback: Callable[[int], None]):
        self.callback = callback
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.error: OSError | None = None

    def start(self) -> None:
        if os.name != "nt":
            raise OSError("全局快捷键仅支持 Windows")
        if self.thread and self.thread.is_alive():
            return
        self.ready.clear()
        self.stopped.clear()
        self.error = None
        self.thread = threading.Thread(target=self._run, name="GlobalShortcut", daemon=True)
        self.thread.start()
        if not self.ready.wait(2):
            raise RuntimeError("全局快捷键注册超时")
        if self.error:
            raise self.error

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        registered: list[tuple[int, int]] = []
        for virtual_key, hotkey_id in self.HOTKEYS.items():
            if user32.RegisterHotKey(None, hotkey_id, self.MOD_NOREPEAT, virtual_key):
                registered.append((hotkey_id, virtual_key))
            else:
                self.error = ctypes.WinError()
                break
        if self.error:
            for hotkey_id, _ in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            registered.clear()
        self.ready.set()
        message = wintypes.MSG()
        try:
            while registered and not self.stopped.is_set():
                if user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    if message.message == 0x0012:
                        break
                    if message.message == self.WM_HOTKEY:
                        try:
                            self.callback(int(message.wParam))
                        except Exception:
                            logger.exception("global hotkey callback failed")
                self.stopped.wait(0.01)
        finally:
            for hotkey_id, _ in registered:
                user32.UnregisterHotKey(None, hotkey_id)
            self.thread_id = 0

    def stop(self) -> None:
        self.stopped.set()
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread:
            self.thread.join(timeout=1)


class InputRecorder:
    """Capture global keyboard transitions and mouse button clicks."""

    def __init__(self):
        self.keyboard_listener = None
        self.mouse_listener = None
        self.recording = False
        self.paused = False
        self._lock = threading.Lock()
        self._held_keys: set[str] = set()
        self.actions: list[dict] = []
        self.pending_actions: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self._start_ns: int | None = None

    @staticmethod
    def _key_name(key) -> str:
        char = getattr(key, "char", None)
        if char:
            return char
        return getattr(key, "name", None) or str(key).replace("'", "")

    @staticmethod
    def _is_control_hotkey(name: str) -> bool:
        return name.casefold() in {"f8", "f9", "f10"}

    def _append_locked(self, action: dict) -> dict:
        now_ns = time.perf_counter_ns()
        if self._start_ns is None:
            self._start_ns = now_ns
        timestamp = now_ns - self._start_ns
        if self.actions:
            timestamp = max(timestamp, self.actions[-1]["timestamp_ns"] + 1)
        action["timestamp_ns"] = timestamp
        self.actions.append(action)
        return action

    def _append(self, action: dict) -> None:
        with self._lock:
            if not self.recording or self.paused:
                return
            stored = self._append_locked(action)
        self.pending_actions.put(stored)

    def drain_pending(self, limit: int = 500) -> list[dict]:
        batch = []
        for _ in range(limit):
            try:
                batch.append(self.pending_actions.get_nowait())
            except queue.Empty:
                break
        return batch

    def start(self) -> None:
        from pynput import keyboard, mouse

        if self.recording:
            raise RuntimeError("录制已经在进行中")
        with self._lock:
            self.actions = []
            while not self.pending_actions.empty():
                try:
                    self.pending_actions.get_nowait()
                except queue.Empty:
                    break
            self._held_keys.clear()
            self._start_ns = None
            self.paused = False
            self.recording = True
        self.keyboard_listener = keyboard.Listener(on_press=self._on_key_down, on_release=self._on_key_up)
        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.keyboard_listener.start()
        self.mouse_listener.start()
        logger.info("recording started")

    def _on_key_down(self, key) -> None:
        try:
            name = self._key_name(key)
            if self._is_control_hotkey(name):
                return
            with self._lock:
                if not self.recording or self.paused or name in self._held_keys:
                    return
                self._held_keys.add(name)
                action = self._append_locked({"type": "key_down", "key": name})
            self.pending_actions.put(action)
        except Exception:
            logger.exception("keyboard key-down callback failed")

    def _on_key_up(self, key) -> None:
        try:
            name = self._key_name(key)
            if self._is_control_hotkey(name):
                return
            with self._lock:
                if name not in self._held_keys:
                    return
                self._held_keys.remove(name)
                if not self.recording:
                    return
                action = self._append_locked({"type": "key_up", "key": name})
            self.pending_actions.put(action)
        except Exception:
            logger.exception("keyboard key-up callback failed")

    def _on_click(self, _x, _y, button, pressed) -> None:
        try:
            if not pressed:
                return
            name = getattr(button, "name", str(button).split(".")[-1])
            if name in ("left", "right", "middle", "x1", "x2"):
                self._append({"type": "click", "button": name})
        except Exception:
            logger.exception("mouse click callback failed")

    def pause(self) -> None:
        with self._lock:
            self.paused = True

    def resume(self) -> None:
        with self._lock:
            self.paused = False

    def stop(self) -> list[dict]:
        with self._lock:
            if not self.recording:
                return list(self.actions)
            self.recording = False
            self.paused = False
            repairs = []
            for key in sorted(self._held_keys):
                repairs.append(self._append_locked({"type": "key_up", "key": key, "repaired": True}))
            self._held_keys.clear()
            result = list(self.actions)
        for action in repairs:
            self.pending_actions.put(action)
        listeners = tuple(listener for listener in (self.keyboard_listener, self.mouse_listener) if listener)
        for listener in listeners:
            try:
                listener.stop()
            except Exception:
                logger.exception("listener stop failed")
        if listeners:
            threading.Thread(
                target=self._join_listeners,
                args=(listeners,),
                name="RecorderCleanup",
                daemon=True,
            ).start()
        logger.info("recording stopped actions=%d repaired_keys=%d", len(result), len(repairs))
        return result

    @staticmethod
    def _join_listeners(listeners) -> None:
        for listener in listeners:
            try:
                listener.join(timeout=2)
            except Exception:
                logger.exception("listener join failed")


class PlaybackWorker(threading.Thread):
    """Inject recorded events using the current global pointer position."""

    SPECIAL_KEYS = {
        "alt": "alt", "alt_l": "alt_l", "alt_r": "alt_r", "alt_gr": "alt_gr",
        "backspace": "backspace", "caps_lock": "caps_lock", "cmd": "cmd", "cmd_l": "cmd_l",
        "cmd_r": "cmd_r", "ctrl": "ctrl", "ctrl_l": "ctrl_l", "ctrl_r": "ctrl_r",
        "delete": "delete", "down": "down", "end": "end", "enter": "enter", "esc": "esc",
        "home": "home", "insert": "insert", "left": "left", "page_down": "page_down",
        "page_up": "page_up", "right": "right", "shift": "shift", "shift_l": "shift_l",
        "shift_r": "shift_r", "space": "space", "tab": "tab", "up": "up",
        "win": "cmd", "windows": "cmd", "print_screen": "print_screen",
        "num_lock": "num_lock", "scroll_lock": "scroll_lock",
    }

    def __init__(self, actions: list[dict], on_action: Callable[[dict], None], on_finished: Callable[[], None]):
        super().__init__(name="ActionPlayback", daemon=True)
        self.actions = actions
        self.on_action = on_action
        self.on_finished = on_finished
        self.stopped = threading.Event()
        self.resume_event = threading.Event()
        self.resume_event.set()
        self.held_keys: set[str] = set()
        self.keyboard = None
        self.error: Exception | None = None
        self._key_objects: dict[str, object] = {}
        self._keyboard_controller = None
        self._mouse_controller = None

    def pause(self) -> None:
        self.resume_event.clear()

    def resume(self) -> None:
        self.resume_event.set()

    def stop(self) -> None:
        self.stopped.set()
        self.resume_event.set()

    @staticmethod
    def _wait_until(deadline_ns: int, stopped: threading.Event, resume: threading.Event) -> bool:
        while not stopped.is_set():
            if not resume.wait(0.01):
                continue
            remaining_ns = deadline_ns - time.perf_counter_ns()
            if remaining_ns <= 0:
                return True
            stopped.wait(min(remaining_ns / 1_000_000_000, 0.002))
        return False

    def _key_object(self, name: str):
        if name not in self._key_objects:
            special = self.SPECIAL_KEYS.get(name.lower())
            if special:
                self._key_objects[name] = getattr(self.keyboard.Key, special)
            elif len(name) == 1:
                self._key_objects[name] = name
            elif name.lower().startswith("f") and name[1:].isdigit():
                self._key_objects[name] = getattr(self.keyboard.Key, name.lower())
            else:
                raise ValueError(f"不支持的按键: {name}")
        return self._key_objects[name]

    def _release_all(self) -> None:
        if self._keyboard_controller is None:
            return
        for name in tuple(self.held_keys):
            try:
                self._keyboard_controller.release(self._key_object(name))
            except Exception:
                pass
            self.held_keys.discard(name)

    def run(self) -> None:
        try:
            from pynput import keyboard, mouse

            self.keyboard = keyboard
            self._keyboard_controller = keyboard.Controller()
            self._mouse_controller = mouse.Controller()
            if not self.actions:
                return
            first_timestamp = int(self.actions[0]["timestamp_ns"])
            start_ns = time.perf_counter_ns() + 100_000_000
            for action in self.actions:
                deadline = start_ns + int(action["timestamp_ns"]) - first_timestamp
                if not self._wait_until(deadline, self.stopped, self.resume_event):
                    break
                kind = action["type"]
                if kind == "key_down":
                    key = self._key_object(action["key"])
                    self._keyboard_controller.press(key)
                    self.held_keys.add(action["key"])
                elif kind == "key_up":
                    key = self._key_object(action["key"])
                    self._keyboard_controller.release(key)
                    self.held_keys.discard(action["key"])
                elif kind == "click":
                    button = getattr(mouse.Button, action.get("button", "left"))
                    self._mouse_controller.press(button)
                    self._mouse_controller.release(button)
                self.on_action(action)
        except Exception as error:
            self.error = error
            logger.exception("playback failed")
        finally:
            self._release_all()
            self.on_finished()
