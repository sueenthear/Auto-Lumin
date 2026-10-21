from __future__ import annotations

import threading
import time

import numpy as np

from ..window_manager import WindowManager


class WindowCapture:
    """Capture a window's client area, reusing a single WGC session.

    Creating a Windows Graphics Capture session per screenshot leaks native
    handles and D3D resources in windows-capture 2.x. Long polling loops such
    as ``do.match.exist(...)`` then keep growing until an allocation fails, so
    one session is kept for the lifetime of this object and its newest frame is
    returned. ``close()`` releases the session explicitly; a window resize
    rebuilds it because the captured crop and dimensions change.
    """

    # A static window produces no new frames. After this long without a newer
    # frame, the cached one still describes the current client area, so it is
    # returned instead of stalling the caller.
    _FRESH_FRAME_WAIT = 0.1

    def __init__(self, window: WindowManager) -> None:
        self.window = window
        self._condition = threading.Condition()
        self._capture = None
        self._control = None
        self._frame: np.ndarray | None = None
        self._sequence = 0
        self._error: BaseException | None = None
        self._closed = False
        self._size: tuple[int, int] | None = None
        self._epoch = 0

    def screenshot(self, timeout: float = 5.0) -> np.ndarray:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        call_start = time.monotonic()
        deadline = call_start + timeout
        cached_ok_at = call_start + min(self._FRESH_FRAME_WAIT, timeout)
        retired: list[tuple] = []
        failure: BaseException | None = None
        wrap = False
        frame: np.ndarray | None = None
        with self._condition:
            current = self.window.client_size
            if self._capture is not None and self._size != current:
                retired.append(self._detach_locked())  # window resized
            if self._capture is None:
                try:
                    self._start(current)
                except BaseException as exc:
                    # If this was a resize, still stop the already-retired
                    # session below before propagating the new-session failure.
                    failure = exc
            epoch = self._epoch
            if failure is None:
                target = self._sequence
                while True:
                    if self._epoch != epoch or self._capture is None:
                        failure = RuntimeError("Window capture session was closed or replaced")
                        break
                    if self._error is not None:
                        failure, wrap = self._error, True
                        self._error = None
                        break
                    if self._closed:
                        failure = RuntimeError(
                            f"Window {self.window.hwnd:#x} closed before a frame arrived")
                        break
                    if self._frame is not None:
                        if self._sequence > target or time.monotonic() >= cached_ok_at:
                            frame = self._frame.copy()
                            break
                    now = time.monotonic()
                    remaining = deadline - now
                    if remaining <= 0:
                        failure = TimeoutError(f"Timed out capturing window {self.window.hwnd:#x}")
                        break
                    # With no frame yet, wait the full timeout. Otherwise wait
                    # only for the freshness window, then reuse the last frame.
                    wait = remaining if self._frame is None else max(0.0, min(remaining, cached_ok_at - now))
                    self._condition.wait(wait)
                # Detach only the session this call waited on. A concurrent
                # close/resize has already detached or replaced that session.
                if failure is not None and self._epoch == epoch and self._capture is not None:
                    retired.append(self._detach_locked())
        # Sessions are stopped outside the lock: callbacks need this same lock,
        # so joining while holding it could deadlock. Also stop a retired session
        # if opening its replacement failed.
        for stale in retired:
            self._stop_session(*stale)
        if frame is not None:
            return frame
        if wrap:
            raise RuntimeError("WGC capture failed") from failure
        if failure is None:
            raise AssertionError("screenshot exited without a frame or failure")
        raise failure

    def close(self) -> None:
        """Stop the capture session and drop its native references."""
        with self._condition:
            retired = self._detach_locked()
        self._stop_session(*retired)

    @property
    def session_active(self) -> bool:
        with self._condition:
            return self._capture is not None

    def _detach_locked(self):
        capture, control = self._capture, self._control
        self._capture = self._control = None
        self._frame = None
        self._sequence = 0
        self._error = None
        self._closed = False
        self._size = None
        # Invalidate screenshots waiting for this session (close/resize/error)
        # and wake them before the native thread is joined outside the lock.
        self._epoch += 1
        self._condition.notify_all()
        return capture, control

    @staticmethod
    def _stop_session(capture, control) -> None:
        try:
            if control is not None:
                try:
                    control.stop()
                finally:
                    control.wait()
        finally:
            if capture is not None:
                # windows-capture 2.x keeps bound Python methods inside the
                # native session; GC cannot reliably see that ownership cycle,
                # so break it explicitly.
                capture.frame_handler = None
                capture.closed_handler = None
                capture.capture = None

    def _start(self, expected_size: tuple[int, int]):
        try:
            from windows_capture import WindowsCapture
        except ImportError as exc:
            raise RuntimeError("Install windows-capture to use WGC screenshots") from exc
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=self.window.hwnd,
        )
        expected_width, expected_height = expected_size

        @capture.event
        def on_frame_arrived(frame, capture_control) -> None:
            try:
                if self._capture is not capture:
                    return  # a retired session must not publish stale frames
                image = frame.frame_buffer
                left, top, right, bottom = self.window.client_rect_in_window_capture()
                frame_height, frame_width = image.shape[:2]
                if left < 0 or top < 0 or right > frame_width or bottom > frame_height:
                    raise RuntimeError(
                        f"Client crop {(left, top, right, bottom)} exceeds WGC frame "
                        f"{(frame_width, frame_height)}"
                    )
                image = image[top:bottom, left:right]
                if image.shape[:2] != (expected_height, expected_width):
                    raise RuntimeError(
                        f"Client capture size {image.shape[1]}x{image.shape[0]} "
                        f"does not match expected {expected_width}x{expected_height}"
                    )
                # Copy only the client area: never retain the whole-frame buffer
                # or the native mapped-frame allocation.
                cropped = image.copy()
            except BaseException as exc:
                with self._condition:
                    if self._capture is capture:
                        self._error = exc
                        self._condition.notify_all()
                return
            with self._condition:
                if self._capture is capture:
                    self._frame = cropped
                    self._sequence += 1
                    self._condition.notify_all()

        @capture.event
        def on_closed() -> None:
            with self._condition:
                if self._capture is capture:
                    self._closed = True
                    self._condition.notify_all()

        self._capture = capture
        self._size = expected_size
        try:
            self._control = capture.start_free_threaded()
        except BaseException:
            self._capture = self._control = self._size = None
            capture.frame_handler = None
            capture.closed_handler = None
            capture.capture = None
            raise