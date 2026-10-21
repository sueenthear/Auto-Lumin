from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from ._logging import DoLogger
from .audio_manager import AudioManager
from .core import ScriptCore
from .image_manager.matcher import TemplateMatcher
from .interrupt import Interrupt
from .pixel import Pixel
from .steps import load_steps


def _log(logger, source: str, message: str) -> None:
    if logger is not None:
        logger.write(source, message)


class _MouseButtons:
    def __init__(self, callback, logger, source):
        self._callback = callback
        self._logger = logger
        self._source = source

    def _invoke(self, button):
        _log(self._logger, f"{self._source}.{button}", button)
        return self._callback(button)

    @property
    def left(self) -> None:
        return self._invoke("left")

    @property
    def right(self) -> None:
        return self._invoke("right")

    @property
    def middle(self) -> None:
        return self._invoke("middle")

    @property
    def xfront(self) -> None:
        return self._invoke("xfront")

    @property
    def xbehind(self) -> None:
        return self._invoke("xbehind")


class _MouseRoll:
    def __init__(self, callback, logger):
        self._callback = callback
        self._logger = logger

    def _invoke(self, direction):
        _log(self._logger, f"do.mouse.roll.{direction}", direction)
        return self._callback(direction)

    @property
    def up(self) -> None:
        return self._invoke("up")

    @property
    def down(self) -> None:
        return self._invoke("down")


class _MoveSettings:
    def __init__(self, move, logger, source):
        self._move = move
        self._logger = logger
        self._source = source

    def abs(self, key: str, value: float) -> None:
        _log(self._logger, f"{self._source}.abs", f"{key}={value}")
        self._move._set("abs", key, value)

    def rel(self, key: str, value: float) -> None:
        _log(self._logger, f"{self._source}.rel", f"{key}={value}")
        self._move._set("rel", key, value)


class _Move:
    def __init__(self, core, logger):
        self._core = core
        self._logger = logger
        self.defaults = {"abs": {"duration": 0.5, "fps": 60}, "rel": {"duration": 0.5, "fps": 60}}
        self.set = _MoveSettings(self, logger, "do.mouse.move.set")

    def abs(
        self, x: int, y: int, duration: float | None = None, fps: int | None = None
    ) -> None:
        _log(self._logger, "do.mouse.move.abs", f"x={x}, y={y}, duration={duration}, fps={fps}")
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
        _log(self._logger, "do.mouse.move.rel", f"dx={dx}, dy={dy}, duration={duration}, fps={fps}")
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

    def __init__(self, core, logger=None):
        self._core = core
        self._click = _MouseButtons(core.input.click, logger, "do.mouse.click")
        self._down = _MouseButtons(core.input.mouse_down, logger, "do.mouse.down")
        self._up = _MouseButtons(core.input.mouse_up, logger, "do.mouse.up")
        self._roll = _MouseRoll(
            lambda direction: core.input.scroll(dy=1 if direction == "up" else -1),
            logger,
        )
        self.move = _Move(core, logger)
        self.set = _MoveSettings(self.move, logger, "do.mouse.set")

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
    def __new__(cls, value, setter, position=None, logger=None):
        result = super().__new__(cls, value)
        result.setter = setter
        result.position = position
        result.logger = logger
        return result

    def __call__(self, *args):
        if len(args) == 2:
            x, y = self.position
            width, height = args
        elif len(args) == 4:
            x, y, width, height = args
        else:
            raise TypeError("size expects (width, height) or (x, y, width, height)")
        _log(self.logger, "do.window.size", f"set ({x}, {y}, {width}, {height})")
        return self.setter(x, y, width, height)


class _CallableString(str):
    def __new__(cls, value, setter, logger=None):
        result = super().__new__(cls, value)
        result.setter = setter
        result.logger = logger
        return result

    def __call__(self, value):
        _log(self.logger, "do.window.title", "set title")
        return self.setter(value)


class _Window:
    def __init__(self, core, logger=None):
        self._core = core
        self._logger = logger
        self._hwnd = core.window.hwnd

    @property
    def hwnd(self):
        _log(self._logger, "do.window.hwnd", "get hwnd")
        return self._hwnd

    @property
    def size(self):
        _log(self._logger, "do.window.size", "get size")
        return _CallableTuple(
            self._core.window.client_size,
            self._size,
            self._core.window.position,
            self._logger,
        )

    def _size(self, x, y, width, height):
        self._core.window.set_position(x, y)
        self._core.window.set_client_size(width, height)

    @property
    def alive(self):
        _log(self._logger, "do.window.alive", "get alive")
        return self._core.window.alive

    @property
    def position(self):
        _log(self._logger, "do.window.position", "get position")
        return self._core.window.position

    @property
    def visible(self):
        _log(self._logger, "do.window.visible", "get visible")
        return self._core.window.visible

    @property
    def minimized(self):
        _log(self._logger, "do.window.minimized", "get minimized")
        return self._core.window.minimized

    def activate(self, timeout=2.0):
        _log(self._logger, "do.window.activate", f"timeout={timeout}")
        return self._core.window.activate(timeout)

    def is_foreground(self):
        _log(self._logger, "do.window.is_foreground", "check foreground")
        return self._core.window.is_foreground()

    def require_foreground(self):
        _log(self._logger, "do.window.require_foreground", "require foreground")
        self._core.window.require_foreground()

    @property
    def title(self):
        _log(self._logger, "do.window.title", "get title")
        return _CallableString(self._core.window.title, self._core.window.set_title, self._logger)


class _Keyboard:
    def __init__(self, core, logger=None):
        self._core = core
        self._logger = logger

    def click(self, key, down_time=0.02):
        _log(self._logger, "do.keyboard.click", f"key={key!r}, down_time={down_time}")
        self._core.input.press_key(key, down_time)

    def down(self, key):
        _log(self._logger, "do.keyboard.down", f"key={key!r}")
        self._core.input.key_down(key)

    def up(self, key):
        _log(self._logger, "do.keyboard.up", f"key={key!r}")
        self._core.input.key_up(key)

    def text(self, text, interval=0.0):
        _log(self._logger, "do.keyboard.text", f"length={len(text)}, interval={interval}")
        self._core.input.type_text(text, interval)


class _Image:
    def __init__(self, core, logger=None):
        self._core = core
        self._logger = logger

    @property
    def screenshot(self):
        _log(self._logger, "do.image.screenshot", "capture screenshot")
        return self._core.image.screenshot()

    def load(self, filepath):
        _log(self._logger, "do.image.load", f"filepath={filepath!s}")
        return TemplateMatcher.load_image(filepath)

    def loads(self, dirpath):
        _log(self._logger, "do.image.loads", f"dirpath={dirpath!s}")
        return {p.stem: TemplateMatcher.load_image(p) for p in Path(dirpath).glob("*.png")}

    def save(self, img, filepath):
        _log(self._logger, "do.image.save", f"filepath={filepath!s}")
        return TemplateMatcher.save_image(filepath, img)

    def close(self):
        _log(self._logger, "do.image.close", "release capture session")
        return self._core.image.close()


class _MatchSettings:
    def __init__(self, match, logger):
        self._match = match
        self._logger = logger

    def printlog(self, value):
        _log(self._logger, "do.match.set.printlog", f"printlog={bool(value)}")
        self._match._printlog = bool(value)

    def way(self, value):
        _log(self._logger, "do.match.set.way", f"way={value!r}")
        self._match._set_way(value)


class _Match:
    def __init__(self, core, logger=None):
        self._core = core
        self._logger = logger
        self._printlog = False
        self._way = 5
        self.set = _MatchSettings(self, logger)

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
        _log(self._logger, "do.match.pos", f"threshold={threshold}, roi={roi}")
        return self._pos(template, source, threshold, roi, title)

    def _pos(self, template, source=None, threshold=0.8, roi=None, title="match!"):
        image = TemplateMatcher.load_image(template) if isinstance(template, (str, Path)) else template
        frame = source if source is not None else self._core.image.screenshot()
        ox = oy = 0
        if roi is not None:
            ox, oy, w, h = roi
            frame = frame[oy:oy+h, ox:ox+w]
        result = TemplateMatcher.find(frame, image, threshold=0, method=self._way)
        score = result.score if result else 0.0
        if self._printlog:
            print(f"{title}: x={None if result is None else result.center[0]+ox}, y={None if result is None else result.center[1]+oy}, score={score}")
        if result is None or score < threshold:
            return None
        return result.center[0] + ox, result.center[1] + oy

    def exist(self, template, source=None, threshold=0.8, roi=None, title="match!"):
        _log(self._logger, "do.match.exist", f"threshold={threshold}, roi={roi}")
        return self._pos(template, source, threshold, roi, title) is not None

class _Execution:
    def __init__(self, core, steps, end, logger=None):
        if end not in ("pause", "abort"):
            raise ValueError("end must be 'pause' or 'abort'")
        self.core, self.steps, self.end = core, steps, end
        self._logger = logger
        self._state = "ready"
        self._closed = False
        self._error = None
        self.elapsed = 0.0
        self._resume = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._worker_thread = None
        self._index = 0
        self._started_at = None
        self._state_changed = threading.Condition()

    def _ensure_active(self):
        if self._closed:
            raise RuntimeError("execution was aborted; create a new execution instance")

    @property
    def state(self):
        with self._state_changed:
            self._ensure_active()
            value = self._state
        _log(self._logger, "do.steps.state", f"{value}")
        return value

    @state.setter
    def state(self, value):
        with self._state_changed:
            self._ensure_active()
            changed = self._state != value
            self._state = value
            if changed:
                self._state_changed.notify_all()
        if changed:
            _log(self._logger, "do.steps.state", f"{value}")

    @property
    def error(self):
        _log(self._logger, "do.steps.error", repr(self._error))
        return self._error

    @error.setter
    def error(self, value):
        self._error = value
        _log(self._logger, "do.steps.error", repr(value))

    @property
    def time(self):
        self._ensure_active()
        value = self.elapsed if self._started_at is None else self.elapsed + time.monotonic() - self._started_at
        _log(self._logger, "do.steps.time", f"{value:.3f}s")
        return value

    def _dispose(self):
        if self._closed:
            return
        with self._state_changed:
            self._closed = True
            self.core = self.steps = self.end = None
            self._resume = self._cancel = self._thread = None
            self._started_at = None
            self.elapsed = 0.0
            self._index = 0
            self._state = None
            self._state_changed.notify_all()


class _Steps:
    def __init__(self, core, logger=None):
        self._core = core
        self._logger = logger

    def load(self, filepath, end="pause"):
        _log(self._logger, "do.steps.load", f"filepath={filepath!s}, end={end}")
        return _Execution(self._core, load_steps(filepath), end, self._logger)

    def loads(self, dirpath, end="pause"):
        _log(self._logger, "do.steps.loads", f"dirpath={dirpath!s}, end={end}")
        return {
            path.stem: _Execution(self._core, load_steps(path), end, self._logger)
            for path in Path(dirpath).glob("*.stepscript")
        }

    @staticmethod
    def info(execution):
        _log(execution._logger, "do.steps.info", "read steps info")
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
        _log(obj._logger, "do.steps.start", "start execution")
        with obj._state_changed:
            obj._ensure_active()
            if obj._state not in ("ready", "paused"):
                raise RuntimeError("execution cannot be started in its current state")
            obj.error = None
            obj._resume.set()
            obj._started_at = time.monotonic()
            if obj._thread is not None and obj._thread.is_alive():
                obj._state = "running"
                obj._state_changed.notify_all()
                _log(obj._logger, "do.steps.state", "running")
                return

            worker = threading.Thread(target=_Steps._run, args=(obj,), daemon=False)
            obj._thread = obj._worker_thread = worker
            try:
                worker.start()
            except Exception:
                obj._thread = obj._worker_thread = None
                obj._started_at = None
                raise
            obj._state = "running"
            obj._state_changed.notify_all()
            _log(obj._logger, "do.steps.state", "running")

    @staticmethod
    def wait(obj):
        _log(obj._logger, "do.steps.wait", "wait for execution to finish")
        with obj._state_changed:
            worker = obj._worker_thread
        if worker is None:
            return
        if worker is threading.current_thread():
            raise RuntimeError("an execution cannot wait for its own worker thread")
        worker.join()

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
                _log(obj._logger, f"do.steps.action.{kind}", str(action.get("key", action.get("button", ""))))
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
                    _log(obj._logger, "do.steps.action.key_up", repr(key))
                    obj.core.input.key_up(key)
                except Exception:
                    pass
            for button in pressed_buttons:
                try:
                    _log(obj._logger, "do.steps.action.mouse_up", repr(button))
                    obj.core.input.mouse_up(button)
                except Exception:
                    pass
            obj.elapsed = max(obj.elapsed, (time.perf_counter_ns()-start)/1e9)
            if obj._cancel.is_set() or obj.end == "abort":
                obj._dispose()

    @staticmethod
    def pause(obj):
        _log(obj._logger, "do.steps.pause", "pause execution")
        if obj.state == "running":
            obj.elapsed = obj.time
            obj._started_at = None
            obj._resume.clear()
            obj.state = "paused"

    @staticmethod
    def abort(obj):
        _log(obj._logger, "do.steps.abort", "abort execution")
        obj._ensure_active()
        obj._cancel.set()
        obj._resume.set()
        thread = obj._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        obj._dispose()

    @staticmethod
    def reset(obj):
        _log(obj._logger, "do.steps.reset", "reset execution")
        obj._ensure_active()
        if obj._thread and obj._thread.is_alive():
            raise RuntimeError("execution must stop before reset")
        obj._cancel.clear(); obj._resume.clear(); obj.elapsed = 0; obj._index = 0
        obj.state = "ready"


class _AudioListen:
    def __init__(self, manager, logger):
        self._manager = manager
        self._logger = logger

    def start(self, template=None) -> None:
        _log(self._logger, "do.audio.listen.start", f"template={template!s}")
        self._manager.start(template)

    def threshold(self, value: float) -> None:
        _log(self._logger, "do.audio.listen.threshold", f"threshold={value}")
        self._manager.set_threshold(value)

    def stop(self) -> None:
        _log(self._logger, "do.audio.listen.stop", "stop listening")
        self._manager.stop()

    @property
    def listening(self) -> bool:
        return self._manager.listening

    @property
    def error(self):
        return self._manager.error


class _Audio:
    def __init__(self, window, logger):
        self._manager = AudioManager(window)
        self._logger = logger
        self.listen = _AudioListen(self._manager, logger)

    def action(self, func) -> None:
        _log(self._logger, "do.audio.action", "register audio action")
        self._manager.set_action(func)


class _LogAPI:
    def __init__(self, logger):
        self._logger = logger

    def output(self, enabled: bool) -> None:
        self._logger.output(enabled)


class AutomationCore:
    mouse: _Mouse

    def __init__(self, core):
        self._core = core
        self._logger = DoLogger()
        self.log = _LogAPI(self._logger)
        self.window = _Window(core, self._logger)
        self.image = _Image(core, self._logger)
        self.pixel = Pixel(core.image.screenshot, self._logger)
        self.match = _Match(core, self._logger)
        self.steps = _Steps(core, self._logger)
        self.mouse = _Mouse(core, self._logger)
        self.keyboard = _Keyboard(core, self._logger)
        self.audio = _Audio(core.window, self._logger)
        self.interrupt = Interrupt(self._logger)

    @property
    def thisfile(self) -> None:
        """Change the working directory to the file accessing this property."""
        caller_path = sys._getframe(1).f_code.co_filename
        _log(getattr(self, "_logger", None), "do.thisfile", f"chdir {Path(caller_path).resolve().parent}")
        os.chdir(Path(caller_path).resolve().parent)

    def wait(self, seconds: float) -> None:
        """Pause the current thread for a nonnegative number of seconds."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("wait seconds must be a finite nonnegative number")
        _log(getattr(self, "_logger", None), "do.wait", f"{seconds}s")
        time.sleep(seconds)
