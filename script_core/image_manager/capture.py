from __future__ import annotations

import threading

import numpy as np

from ..window_manager import WindowManager


class WindowCapture:
    """Capture one frame from a window using Windows Graphics Capture."""

    def __init__(self, window: WindowManager) -> None:
        self.window = window

    def screenshot(self, timeout: float = 5.0) -> np.ndarray:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        try:
            from windows_capture import WindowsCapture
        except ImportError as exc:
            raise RuntimeError("Install windows-capture to use WGC screenshots") from exc

        frame_ready = threading.Event()
        result: list[np.ndarray] = []
        errors: list[BaseException] = []
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=self.window.hwnd,
        )

        @capture.event
        def on_frame_arrived(frame, capture_control) -> None:
            if not result and not errors:
                try:
                    image = frame.frame_buffer.copy()
                    left, top, right, bottom = self.window.client_rect_in_window_capture()
                    frame_height, frame_width = image.shape[:2]
                    if left < 0 or top < 0 or right > frame_width or bottom > frame_height:
                        raise RuntimeError(
                            f"Client crop {(left, top, right, bottom)} exceeds WGC frame "
                            f"{(frame_width, frame_height)}"
                        )
                    image = image[top:bottom, left:right]
                    expected_width, expected_height = self.window.client_size
                    if image.shape[:2] != (expected_height, expected_width):
                        raise RuntimeError(
                            f"Client capture size {image.shape[1]}x{image.shape[0]} "
                            f"does not match expected {expected_width}x{expected_height}"
                        )
                    result.append(image)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    capture_control.stop()
                    frame_ready.set()

        @capture.event
        def on_closed() -> None:
            frame_ready.set()

        control = capture.start_free_threaded()
        if not frame_ready.wait(timeout):
            control.stop()
            control.wait()
            raise TimeoutError(f"Timed out capturing window {self.window.hwnd:#x}")
        control.wait()
        if errors:
            raise RuntimeError("WGC capture failed") from errors[0]
        if not result:
            raise RuntimeError(f"Window {self.window.hwnd:#x} closed before a frame arrived")
        return result[0]
