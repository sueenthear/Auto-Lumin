from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from .core import ScriptCore
from .image_manager.matcher import TemplateMatcher
from .interrupt import Interrupt
from .pixel import Pixel
from .steps import load_steps


class _MouseButtons:
    def __init__(self, callback):
        self._callback = callback

    @property
    def left(self) -> None:
        return self._callback("left")

    @property
    def right(self) -> None:
        return self._callback("right")

    @property
    def middle(self) -> None:
        return self._callback("middle")

    @property
    def xfront(self) -> None:
        return self._callback("xfront")

    @property
    def xbehind(self) -> None:
        return self._callback("xbehind")


class _MouseRoll:
    def __init__(self, callback):
        self._callback = callback

    @property
    def up(self) -> None:
        return self._callback("up")

    @property
    def down(self) -> None:
        return self._callback("down")


class _MoveSettings:
    def __init__(self, move):
        self._move = move

    def abs(self, key: str, value: float) -> None:
        self._move._set("abs", key, value)

    def rel(self, key: str, value: float) -> None:
        self._move._set("rel", key, value)


class _Move:
    def __init__(self, core):
        self._core = core
        self.defaults = {"abs": {"duration": 0.5, "fps": 60}, "rel": {"duration": 0.5, "fps": 60}}
        self.set = _MoveSettings(self)

    def abs(
        self, x: int, y: int, duration: float | None = None, fps: int | None = None
    ) -> None:
        settings = self.defaults["abs"]
        self._core.input.move_to(
            x,
            y,
            duration=settings["duration"] if duration is None else duration,
            fps=settings["fps"] if fps is None else fps,
        )

    def rel(
        self, dx: int, dy: int, duration: float | None = None, fps: int | None = None
    ) -> None:
        settings = self.defaults["rel"]
        self._core.input.move_by(
            dx,
            dy,
            duration=settings["duration"] if duration is None else duration,
            fps=settings["fps"] if fps is None else fps,
        )

    def _set(self, mode: str, key: str, value: float) -> None:
        if key not in ("duration", "fps") or value < 0 or (key == "fps" and value < 1):
            raise ValueError("invalid mouse move setting")
        self.defaults[mode][key] = value


class _Mouse:
    click: _MouseButtons
    down: _MouseButtons
    up: _MouseButtons
    roll: _MouseRoll
    move: _Move
    set: _MoveSettings

    def __init__(self, core):
        self._core = core
        self._click = _MouseButtons(core.input.click)
        self._down = _MouseButtons(core.input.mouse_down)
        self._up = _MouseButtons(core.input.mouse_up)
        self._roll = _MouseRoll(
            lambda direction: core.input.scroll(dy=1 if direction == "up" else -1)
        )
        self.move = _Move(core)
        self.set = self.move.set

    @property
    def click(self) -> _MouseButtons:
        return self._click

    @property
    def down(self) -> _MouseButtons:
        return self._down

    @property
    def up(self) -> _MouseButtons:
        return self._up

    @property
    def roll(self) -> _MouseRoll:
        return self._roll


class _CallableTuple(tuple):
    def __new__(cls, value, setter, position=None):
        result = super().__new__(cls, value)
        result.setter = setter
        result.position = position
        return result

    def __call__(self, *args):
        if len(args) == 2:
            x, y = self.position
            width, height = args
        elif len(args) == 4:
            x, y, width, height = args
        else:
            raise TypeError("size expects (width, height) or (x, y, width, height)")
        return self.setter(x, y, width, height)


class _CallableString(str):
    def __new__(cls, value, setter):
        result = super().__new__(cls, value)
        result.setter = setter
        return result

    def __call__(self, value):
        return self.setter(value)


class _Window:
    def __init__(self, core):
        self._core = core
        self.hwnd = core.window.hwnd

    @property
    def size(self):
        return _CallableTuple(
            self._core.window.client_size,
            self._size,
            self._core.window.position,
        )

    def _size(self, x, y, width, height):
        self._core.window.set_position(x, y)
        self._core.window.set_client_size(width, height)

    @property
    def alive(self):
        return self._core.window.alive

    @property
    def position(self):
        return self._core.window.position

    @property
    def visible(self):
        return self._core.window.visible

    @property
    def minimized(self):
        return self._core.window.minimized

    def activate(self, timeout=2.0):
        return self._core.window.activate(timeout)

    def is_foreground(self):
        return self._core.window.is_foreground()

    def require_foreground(self):
        self._core.window.require_foreground()

    @property
    def title(self):
        return _CallableString(self._core.window.title, self._core.window.set_title)


class _Keyboard:
    def __init__(self, core):
        self._core = core

    def click(self, key, down_time=0.02):
        self._core.input.press_key(key, down_time)

    def down(self, key):
        self._core.input.key_down(key)

    def up(self, key):
        self._core.input.key_up(key)

    def text(self, text, interval=0.0):
        self._core.input.type_text(text, interval)


class _Image:
    def __init__(self, core):
        self._core = core

    @property
    def screenshot(self):
        return self._core.image.screenshot()

    @staticmethod
    def load(filepath):
        return TemplateMatcher.load_image(filepath)

    @staticmethod
    def loads(dirpath):
        return {p.stem: TemplateMatcher.load_image(p) for p in Path(dirpath).glob("*.png")}

    @staticmethod
    def save(img, filepath):
        TemplateMatcher.save_image(filepath, img)


class _Match:
    def __init__(self, core):
        self._core = core
        self._printlog = False
        self._way = 5
        self.set = type("MatchSettings", (), {"printlog": lambda _, value: setattr(self, "_printlog", bool(value)), "way": lambda _, value: self._set_way(value)})()

    def _set_way(self, way):
        import cv2
        methods = {
            "ccoeff": cv2.TM_CCOEFF_NORMED,
            "sqdiff": cv2.TM_SQDIFF_NORMED,
            "ccorr": cv2.TM_CCORR_NORMED,
            "TM_CCOEFF_NORMED": cv2.TM_CCOEFF_NORMED,
            "TM_SQDIFF_NORMED": cv2.TM_SQDIFF_NORMED,
            "TM_CCORR_NORMED": cv2.TM_CCORR_NORMED,
        }
        if way not in methods:
            raise ValueError(f"unsupported match way: {way}")
        self._way = methods[way]

    def pos(self, template, source=None, threshold=0.8, roi=None, title="match!"):
        import cv2
        image = TemplateMatcher.load_image(template) if isinstance(template, (str, Path)) else template
        frame = source if source is not None else self._core.image.screenshot()
        ox = oy = 0
        if roi is not None:
            ox, oy, w, h = roi
            frame = frame[oy:oy+h, ox:ox+w]
        result = TemplateMatcher.find(frame, image, threshold=0)
        score = result.score if result else 0.0
        if result and self._way != 5:
            method = self._way
            needle = TemplateMatcher._to_bgr(image)
            haystack = TemplateMatcher._to_bgr(frame)
            values = cv2.matchTemplate(haystack, needle, method)
            low, high, low_pos, high_pos = cv2.minMaxLoc(values)
            loc, score = (low_pos, 1 - low) if method == 1 else (high_pos, high)
            result = type("Match", (), {"center": (loc[0] + needle.shape[1]//2, loc[1] + needle.shape[0]//2)})()
        if self._printlog:
            print(f"{title}: x={None if result is None else result.center[0]+ox}, y={None if result is None else result.center[1]+oy}, score={score}")
        if result is None or score < threshold:
            return None
        return result.center[0] + ox, result.center[1] + oy

    def exist(self, *args, **kwargs):
        return self.pos(*args, **kwargs) is not None


class _Execution:
    def __init__(self, core, steps, end):
        if end not in ("pause", "abort"):
            raise ValueError("end must be 'pause' or 'abort'")
        self.core, self.steps, self.end = core, steps, end
        self._state = "ready"
        self._closed = False
        self.error = None
        self.elapsed = 0.0
        self._resume = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._index = 0
        self._started_at = None

    def _ensure_active(self):
        if self._closed:
            raise RuntimeError("execution was aborted; create a new execution instance")

    @property
    def state(self):
        self._ensure_active()
        return self._state

    @state.setter
    def state(self, value):
        self._ensure_active()
        self._state = value

    @property
    def time(self):
        self._ensure_active()
        if self._started_at is None:
            return self.elapsed
        return self.elapsed + time.monotonic() - self._started_at

    def _dispose(self):
        if self._closed:
            return
        self._closed = True
        self.core = self.steps = self.end = None
        self._resume = self._cancel = self._thread = None
        self._started_at = None
        self.elapsed = 0.0
        self._index = 0
        self._state = None


class _Steps:
    def __init__(self, core):
        self._core = core

    def load(self, filepath, end="pause"):
        return _Execution(self._core, load_steps(filepath), end)

    def loads(self, dirpath, end="pause"):
        return {
            path.stem: _Execution(self._core, load_steps(path), end)
            for path in Path(dirpath).glob("*.stepscript")
        }

    @staticmethod
    def info(execution):
        execution._ensure_active()
        actions = execution.steps["actions"]
        duration_ns = execution.steps.get("info", {}).get(
            "duration_ns",
            actions[-1]["timestamp_ns"] if actions else 0,
        )
        return {
            "format": execution.steps["format"],
            "version": execution.steps["version"],
            "timestamp_unit": execution.steps["timestamp_unit"],
            "info": {
                "duration_ns": duration_ns,
                "action_count": len(actions),
            },
        }

    @staticmethod
    def start(obj):
        if obj.state not in ("ready", "paused"):
            raise RuntimeError("execution cannot be started in its current state")
        obj.error = None
        obj._resume.set()
        obj._started_at = time.monotonic()
        obj.state = "running"
        if obj._thread is not None and obj._thread.is_alive():
            return
        obj._thread = threading.Thread(target=_Steps._run, args=(obj,), daemon=False)
        obj._thread.start()

    @staticmethod
    def _run(obj):
        start = time.perf_counter_ns() - int(obj.elapsed * 1e9)
        pressed_keys = set()
        pressed_buttons = set()
        try:
            actions = obj.steps["actions"]
            obj.state = "running"
            for index in range(obj._index, len(actions)):
                action = actions[index]
                while not obj._resume.is_set():
                    if obj._cancel.is_set():
                        return
                    time.sleep(0.01)
                    start += int(0.01 * 1e9)
                if obj._cancel.is_set():
                    return
                deadline = start + action["timestamp_ns"]
                while obj._resume.is_set() and not obj._cancel.is_set():
                    remaining_ns = deadline - time.perf_counter_ns()
                    if remaining_ns <= 0:
                        break
                    time.sleep(min(0.01, remaining_ns / 1e9))
                if obj._cancel.is_set():
                    return
                if not obj._resume.is_set():
                    pause_start = time.perf_counter_ns()
                    obj.elapsed = max(0, (pause_start-start)/1e9)
                    obj._started_at = None
                    obj.state = "paused"
                    while not obj._resume.wait(0.01):
                        if obj._cancel.is_set():
                            return
                    if obj._cancel.is_set():
                        return
                    start += time.perf_counter_ns() - pause_start
                    obj._started_at = time.monotonic()
                    obj.state = "running"
                kind = action["type"]
                if kind == "key_down":
                    key = action["key"]
                    obj.core.input.key_down(key)
                    pressed_keys.add(key)
                elif kind == "key_up":
                    key = action["key"]
                    obj.core.input.key_up(key)
                    pressed_keys.discard(key)
                elif kind == "mouse_down":
                    button = action["button"]
                    obj.core.input.mouse_down(button)
                    pressed_buttons.add(button)
                elif kind == "mouse_up":
                    button = action["button"]
                    obj.core.input.mouse_up(button)
                    pressed_buttons.discard(button)
                else:
                    obj.core.input.click(action["button"])
                obj._index = index + 1
                obj.elapsed = action["timestamp_ns"] / 1e9
            if obj.end == "pause":
                obj.state = "paused"
        except Exception as exc:
            obj.error = exc
            obj.state = "failed"
        finally:
            obj._started_at = None
            for key in pressed_keys:
                try:
                    obj.core.input.key_up(key)
                except Exception:
                    pass
            for button in pressed_buttons:
                try:
                    obj.core.input.mouse_up(button)
                except Exception:
                    pass
            obj.elapsed = max(obj.elapsed, (time.perf_counter_ns()-start)/1e9)
            if obj._cancel.is_set() or obj.end == "abort":
                obj._dispose()

    @staticmethod
    def pause(obj):
        if obj.state == "running":
            obj.elapsed = obj.time
            obj._started_at = None
            obj._resume.clear()
            obj.state = "paused"

    @staticmethod
    def abort(obj):
        obj._ensure_active()
        obj._cancel.set()
        obj._resume.set()
        thread = obj._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        obj._dispose()

    @staticmethod
    def reset(obj):
        obj._ensure_active()
        if obj._thread and obj._thread.is_alive():
            raise RuntimeError("execution must stop before reset")
        obj._cancel.clear(); obj._resume.clear(); obj.elapsed = 0; obj._index = 0
        obj.state = "ready"


class AutomationCore:
    mouse: _Mouse

    def __init__(self, core):
        self._core = core
        self.window, self.image = _Window(core), _Image(core)
        self.pixel = Pixel(self.image.screenshot)
        self.match, self.steps = _Match(core), _Steps(core)
        self.mouse = _Mouse(core)
        self.keyboard = _Keyboard(core)
        self.interrupt = Interrupt()

    @property
    def thisfile(self) -> None:
        """Change the working directory to the file accessing this property."""
        caller_path = sys._getframe(1).f_code.co_filename
        os.chdir(Path(caller_path).resolve().parent)

    @staticmethod
    def wait(seconds: float) -> None:
        """Pause the current thread for a nonnegative number of seconds."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("wait seconds must be a finite nonnegative number")
        time.sleep(seconds)
