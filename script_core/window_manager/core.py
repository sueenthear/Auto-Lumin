from __future__ import annotations

import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes
from typing import Any, Iterator

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

    @contextmanager
    def _physical_dpi_context(self) -> Iterator[None]:
        """Make Win32 coordinate queries on this thread return physical pixels.

        This is a fallback for processes that already created a window before
        importing :mod:`script_core` (so process-wide DPI awareness could no
        longer be enabled).  WGC frames are always physical; without the
        override an unaware process gets virtualized client coordinates and
        crops the wrong region.
        """
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            set_context = user32.SetThreadDpiAwarenessContext
            set_context.argtypes = [ctypes.c_void_p]
            set_context.restype = ctypes.c_void_p
        except (AttributeError, OSError):
            yield
            return
        previous = set_context(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        try:
            yield
        finally:
            if previous:
                set_context(previous)

    @property
    def client_size_physical(self) -> tuple[int, int]:
        """Return the client size in physical pixels on any DPI setting."""
        with self._physical_dpi_context():
            return self.client_size

    def client_to_screen(self, x: int, y: int) -> tuple[int, int]:
        self._ensure_window()
        return win32gui.ClientToScreen(self.hwnd, (x, y))

    def screen_to_client(self, x: int, y: int) -> tuple[int, int]:
        self._ensure_window()
        return win32gui.ScreenToClient(self.hwnd, (x, y))

    def extended_frame_bounds(self) -> tuple[int, int, int, int]:
        """Return DWM's visible window bounds in the current thread's DPI space.

        A WGC window frame starts at the same top-left corner as these bounds,
        so they convert between screen coordinates and frame pixels.  Call it
        inside :meth:`_physical_dpi_context` before mixing the result with
        captured pixels, because WGC frames are always physical.
        """
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
        return visible.left, visible.top, visible.right, visible.bottom

    def client_rect_in_window_capture(self) -> tuple[int, int, int, int]:
        """Return the client-area crop rectangle in physical WGC frame pixels."""
        with self._physical_dpi_context():
            visible_left, visible_top, _, _ = self.extended_frame_bounds()
            client_left, client_top = self.client_to_screen(0, 0)
            width, height = self.client_size
        x = client_left - visible_left
        y = client_top - visible_top
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
        outer = wintypes.RECT(0, 0, width, height)
        adjust = ctypes.WinDLL("user32", use_last_error=True).AdjustWindowRectEx
        adjust.argtypes = (ctypes.POINTER(wintypes.RECT), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        adjust.restype = wintypes.BOOL
        if not adjust(ctypes.byref(outer), style, bool(menu), extended_style):
            raise ctypes.WinError(ctypes.get_last_error())
        outer_width = outer.right - outer.left
        outer_height = outer.bottom - outer.top
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
