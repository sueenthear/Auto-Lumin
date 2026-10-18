from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

import win32con
import win32gui
import win32process
import win32com.client


class WindowManager:
    """Find and manage a top-level Windows application window."""

    def __init__(
        self,
        *,
        hwnd: int | None = None,
        title: str | None = None,
        executable_name: str | None = None,
    ) -> None:
        selectors = sum(value is not None for value in (hwnd, title, executable_name))
        if selectors != 1:
            raise ValueError("Specify exactly one of hwnd, title, or executable_name")
        self.hwnd = hwnd if hwnd is not None else self._find_window(title, executable_name)
        self._ensure_window()

    @staticmethod
    def _find_window(title: str | None, executable_name: str | None) -> int:
        matches: list[int] = []

        def visit(hwnd: int, _: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if title is not None:
                if title.casefold() in win32gui.GetWindowText(hwnd).casefold():
                    matches.append(hwnd)
                    return False
                return True
            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            try:
                process = win32com.client.GetObject(
                    f"winmgmts:Win32_Process.Handle='{process_id}'"
                )
                name = str(process.Name)
            except Exception:
                return True
            if name.casefold() == executable_name.casefold():
                matches.append(hwnd)
                return False
            return True

        win32gui.EnumWindows(visit, None)
        if not matches:
            selector = title if title is not None else executable_name
            raise LookupError(f"No visible window found matching {selector!r}")
        return matches[0]

    def _ensure_window(self) -> None:
        if not win32gui.IsWindow(self.hwnd):
            raise ValueError(f"Invalid window handle: {self.hwnd!r}")

    @property
    def alive(self) -> bool:
        return bool(self.hwnd and win32gui.IsWindow(self.hwnd))

    @property
    def is_alive(self) -> bool:
        return self.alive

    @property
    def visible(self) -> bool:
        return bool(self.alive and win32gui.IsWindowVisible(self.hwnd))

    @property
    def minimized(self) -> bool:
        return bool(self.alive and win32gui.IsIconic(self.hwnd))

    @property
    def title(self) -> str:
        self._ensure_window()
        return win32gui.GetWindowText(self.hwnd)

    @property
    def process_id(self) -> int:
        self._ensure_window()
        return win32process.GetWindowThreadProcessId(self.hwnd)[1]

    @property
    def position(self) -> tuple[int, int]:
        self._ensure_window()
        left, top, _, _ = win32gui.GetWindowRect(self.hwnd)
        return left, top

    @property
    def size(self) -> tuple[int, int]:
        """Return the client-area size used by the public API."""
        return self.client_size

    @property
    def outer_size(self) -> tuple[int, int]:
        self._ensure_window()
        left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
        return right - left, bottom - top

    @property
    def client_size(self) -> tuple[int, int]:
        self._ensure_window()
        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        return right - left, bottom - top

    def client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        self._ensure_window()
        return win32gui.ClientToScreen(self.hwnd, (x, y))

    def screen_to_client(self, x: int, y: int) -> tuple[int, int]:
        self._ensure_window()
        return win32gui.ScreenToClient(self.hwnd, (x, y))

    def client_rect_in_window_capture(self) -> tuple[int, int, int, int]:
        """Return the client-area crop rectangle in a WGC window frame."""
        self._ensure_window()
        visible = wintypes.RECT()
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            self.hwnd,
            9,
            ctypes.byref(visible),
            ctypes.sizeof(visible),
        )
        if result != 0:
            raise OSError(result, "DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS) failed")

        client_left, client_top = self.client_to_screen(0, 0)
        x = client_left - visible.left
        y = client_top - visible.top
        width, height = self.client_size
        return x, y, x + width, y + height

    def set_position(self, x: int, y: int) -> None:
        self._ensure_window()
        win32gui.SetWindowPos(
            self.hwnd,
            None,
            x,
            y,
            0,
            0,
            win32con.SWP_NOSIZE | win32con.SWP_NOZORDER,
        )

    def set_title(self, title: str) -> None:
        self._ensure_window()
        win32gui.SetWindowText(self.hwnd, title)

    def set_client_size(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("Client dimensions must be positive")
        self._ensure_window()
        style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_STYLE)
        extended_style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
        menu = win32gui.GetMenu(self.hwnd)
        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        outer = win32gui.AdjustWindowRectEx(
            (left, top, left + width, top + height), style, bool(menu), extended_style
        )
        outer_width = outer[2] - outer[0]
        outer_height = outer[3] - outer[1]
        win32gui.SetWindowPos(
            self.hwnd,
            None,
            0,
            0,
            outer_width,
            outer_height,
            win32con.SWP_NOMOVE | win32con.SWP_NOZORDER,
        )

    def set_size(self, width: int, height: int) -> None:
        self.set_client_size(width, height)

    def activate(self, timeout: float = 2.0) -> bool:
        self._ensure_window()
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except win32gui.error:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            foreground = win32gui.GetForegroundWindow()
            if foreground == self.hwnd or win32gui.IsChild(self.hwnd, foreground):
                return True
            time.sleep(0.01)
        return win32gui.GetForegroundWindow() == self.hwnd

    def is_foreground(self) -> bool:
        self._ensure_window()
        return win32gui.GetForegroundWindow() == self.hwnd

    def require_foreground(self) -> None:
        if not self.is_foreground() and not self.activate():
            raise RuntimeError(f"Could not activate window {self.title!r}")
