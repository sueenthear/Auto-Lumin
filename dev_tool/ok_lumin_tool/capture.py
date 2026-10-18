from __future__ import annotations

import ctypes
import os
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Callable

try:
    import mouse
    import psutil
    import win32gui
    import win32process
    from PIL import ImageGrab
except ImportError:  # pragma: no cover - dependency check is handled by the UI
    mouse = None
    psutil = None
    win32gui = None
    win32process = None
    ImageGrab = None


class CaptureController:
    """Manage one crosshair overlay and one resizable capture-region overlay."""

    def __init__(self, root, on_status: Callable[[str], None]):
        self.root = root
        self.on_status = on_status
        self.hwnd: int | None = None
        self.crosshair = None
        self.region = None
        self.crosshair_visible = True
        self.region_visible = True
        self._crosshair_moved_unbound = False
        self._region_moved_unbound = False
        self.crosshair_size = 42
        self.point_data = {"x": 100, "y": 100}
        self.crosshair_xy = [100, 100]
        self.region_data = {"x": 180, "y": 120, "w": 320, "h": 220}
        self.region_alpha = 0.42
        self.crosshair_alpha = 0.45
        self._crosshair_drag = None
        self._region_drag = None
        self._region_resize = None
        self._build_overlays()

    @property
    def available(self) -> bool:
        return all(item is not None for item in (mouse, psutil, win32gui, win32process, ImageGrab))

    def _build_overlays(self) -> None:
        self.crosshair = self._make_overlay("#ff3b30", self.crosshair_alpha)
        self.crosshair.title("十字准心")
        canvas = self._canvas(self.crosshair, "#ff3b30")
        size = self.crosshair_size
        canvas.create_line(size // 2, 0, size // 2, size, fill="white", width=2)
        canvas.create_line(0, size // 2, size, size // 2, fill="white", width=2)
        canvas.create_oval(size // 2 - 3, size // 2 - 3, size // 2 + 3, size // 2 + 3, fill="#ff3b30", outline="white")
        canvas.bind("<ButtonPress-1>", self._start_crosshair_drag)
        canvas.bind("<B1-Motion>", self._drag_crosshair)
        canvas.bind("<ButtonRelease-1>", self._finish_crosshair_drag)
        canvas.bind("<Double-Button-1>", lambda _event: self.copy_crosshair_position())
        canvas.bind("<MouseWheel>", self._adjust_crosshair_alpha)

        self.region = self._make_overlay("#2f80ed", self.region_alpha)
        self.region.title("区域框")
        self.region.configure(bg="#2f80ed")
        self.region.attributes("-alpha", self.region_alpha)
        region_canvas = self._canvas(self.region, "#2f80ed")
        self.region_canvas = region_canvas
        self._draw_region_frame()
        region_canvas.bind("<Configure>", lambda event: self._draw_region_frame(event.width, event.height))
        region_canvas.bind("<ButtonPress-1>", self._start_region_drag)
        region_canvas.bind("<B1-Motion>", self._drag_region)
        region_canvas.bind("<ButtonRelease-1>", self._finish_region_drag)
        region_canvas.bind("<ButtonPress-3>", self._start_region_resize)
        region_canvas.bind("<B3-Motion>", self._resize_region)
        region_canvas.bind("<ButtonRelease-3>", self._finish_region_resize)
        region_canvas.bind("<Double-Button-1>", lambda _event: self.safe_capture("full"))
        region_canvas.bind("<Double-Button-3>", lambda _event: self.safe_capture("area"))
        region_canvas.bind("<MouseWheel>", self._adjust_region_alpha)

        self._place_overlays()
        self.set_visible(True)

    def _draw_region_frame(self, width: int | None = None, height: int | None = None) -> None:
        self.region_canvas.delete("frame")
        width = max(20, width or self.region.winfo_width())
        height = max(20, height or self.region.winfo_height())
        self.region_canvas.create_rectangle(2, 2, width - 2, height - 2,
                                            outline="#5db7ff", width=3, tags="frame")
        self.region_canvas.create_text(10, 10, text="区域", fill="white", anchor="nw",
                                       font=("Segoe UI", 10, "bold"), tags="frame")

    def _make_overlay(self, color: str, alpha: float):
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.wm_attributes("-topmost", True)
        overlay.attributes("-alpha", alpha)
        overlay.configure(bg=color)
        return overlay

    @staticmethod
    def _canvas(window, color: str):
        import tkinter as tk

        canvas = tk.Canvas(window, bg=color, highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        return canvas

    def _place_overlays(self) -> None:
        origin_x, origin_y = self.client_origin()
        x, y = self.crosshair_xy
        self.crosshair.geometry(f"{self.crosshair_size}x{self.crosshair_size}+{x}+{y}")
        data = self.region_data
        self.region.geometry(
            f"{data['w']}x{data['h']}+{data['x'] + origin_x}+{data['y'] + origin_y}"
        )

    def destroy(self) -> None:
        for overlay in (self.crosshair, self.region):
            if overlay is not None and overlay.winfo_exists():
                overlay.destroy()

    def set_visible(self, visible: bool) -> None:
        self.crosshair_visible = visible
        method = "deiconify" if visible else "withdraw"
        getattr(self.crosshair, method)()

    def set_region_visible(self, visible: bool) -> None:
        self.region_visible = visible
        (self.region.deiconify if visible else self.region.withdraw)()

    def get_hwnd_by_exe(self, exe_name: str, timeout: float = 3.0):
        if not self.available:
            raise RuntimeError("位置工具依赖未安装，请先安装 requirements.txt")
        if not exe_name:
            return win32gui.GetDesktopWindow()
        end_time = time.time() + timeout
        while time.time() < end_time:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if not proc.info["name"] or proc.info["name"].casefold() != exe_name.casefold():
                        continue
                    pid = proc.info["pid"]
                    hwnds = []

                    def callback(hwnd, result):
                        if win32gui.IsWindowVisible(hwnd):
                            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                            if found_pid == pid:
                                result.append(hwnd)
                        return True

                    win32gui.EnumWindows(callback, hwnds)
                    if hwnds:
                        return hwnds[0]
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            time.sleep(0.2)
        return None

    def bind_window(self, exe_name: str) -> int | None:
        previous_hwnd = self.hwnd
        hwnd = self.get_hwnd_by_exe(exe_name.strip())
        if hwnd:
            if hwnd != previous_hwnd:
                self.hwnd = hwnd
                origin_x, origin_y = self.client_origin()
                if previous_hwnd is None and self._crosshair_moved_unbound:
                    self.point_data["x"] = self.crosshair.winfo_x() + self.crosshair_size // 2 - origin_x
                    self.point_data["y"] = self.crosshair.winfo_y() + self.crosshair_size // 2 - origin_y
                if previous_hwnd is None and self._region_moved_unbound:
                    self.region_data["x"] = self.region.winfo_x() - origin_x
                    self.region_data["y"] = self.region.winfo_y() - origin_y
                self._crosshair_moved_unbound = False
                self._region_moved_unbound = False
            else:
                self.hwnd = hwnd
            self.sync_overlay_to_data()
            (self.crosshair.deiconify if self.crosshair_visible else self.crosshair.withdraw)()
            (self.region.deiconify if self.region_visible else self.region.withdraw)()
            self.on_status(f"已绑定窗口 HWND={self.hwnd}")
        else:
            self.on_status("未找到匹配窗口")
        return self.hwnd

    def client_origin(self) -> tuple[int, int]:
        if self.hwnd:
            return win32gui.ClientToScreen(self.hwnd, (0, 0))
        return 0, 0

    def sync_overlay_to_data(self) -> None:
        """Move both overlays from stored client-area coordinates."""
        origin_x, origin_y = self.client_origin()
        self.crosshair_xy[:] = [
            origin_x + self.point_data["x"] - self.crosshair_size // 2,
            origin_y + self.point_data["y"] - self.crosshair_size // 2,
        ]
        self._place_overlays()

    def sync_data_to_overlay(self) -> None:
        """Read overlay geometry without changing client coordinates before binding."""
        if not self.hwnd:
            return
        origin_x, origin_y = self.client_origin()
        self.point_data["x"] = self.crosshair.winfo_x() + self.crosshair_size // 2 - origin_x
        self.point_data["y"] = self.crosshair.winfo_y() + self.crosshair_size // 2 - origin_y
        self.region_data["x"] = self.region.winfo_x() - origin_x
        self.region_data["y"] = self.region.winfo_y() - origin_y
        self.region_data["w"] = self.region.winfo_width()
        self.region_data["h"] = self.region.winfo_height()
        self.crosshair_xy[:] = [self.crosshair.winfo_x(), self.crosshair.winfo_y()]

    def apply_region_data(self, data: dict[str, int]) -> None:
        """Apply client-area region data without changing the independent crosshair."""
        self.region_data.update(data)
        origin_x, origin_y = self.client_origin()
        self.region.geometry(
            f"{self.region_data['w']}x{self.region_data['h']}+"
            f"{self.region_data['x'] + origin_x}+{self.region_data['y'] + origin_y}"
        )

    def get_crosshair_position(self) -> tuple[int, int]:
        self.sync_data_to_overlay()
        return self.point_data["x"], self.point_data["y"]

    def get_region_data(self) -> dict[str, int]:
        self.sync_data_to_overlay()
        return dict(self.region_data)

    def copy_crosshair_position(self) -> None:
        x, y = self.get_crosshair_position()
        self.root.clipboard_clear()
        self.root.clipboard_append(f"({x},{y})")
        self.on_status(f"已复制准心位置 ({x},{y})")

    def _start_crosshair_drag(self, event) -> None:
        self._crosshair_drag = (event.x_root, event.y_root, self.crosshair.winfo_x(), self.crosshair.winfo_y())

    def _drag_crosshair(self, event) -> None:
        if not self._crosshair_drag:
            return
        start_x, start_y, origin_x, origin_y = self._crosshair_drag
        x = origin_x + event.x_root - start_x
        y = origin_y + event.y_root - start_y
        self.crosshair.geometry(f"+{x}+{y}")
        self.crosshair_xy[:] = [x, y]
        origin_x, origin_y = self.client_origin()
        self.point_data["x"] = x + self.crosshair_size // 2 - origin_x
        self.point_data["y"] = y + self.crosshair_size // 2 - origin_y
        if not self.hwnd:
            self._crosshair_moved_unbound = True

    def _finish_crosshair_drag(self, _event) -> None:
        self._crosshair_drag = None

    def _start_region_drag(self, event) -> None:
        self._region_drag = (event.x_root, event.y_root, self.region.winfo_x(), self.region.winfo_y())

    def _drag_region(self, event) -> None:
        if not self._region_drag:
            return
        start_x, start_y, origin_x, origin_y = self._region_drag
        x = origin_x + event.x_root - start_x
        y = origin_y + event.y_root - start_y
        self.region.geometry(f"+{x}+{y}")
        if self.region_data:
            client_x, client_y = self.client_origin()
            self.region_data["x"] = x - client_x
            self.region_data["y"] = y - client_y
            if not self.hwnd:
                self._region_moved_unbound = True

    def _finish_region_drag(self, _event) -> None:
        self._region_drag = None

    def _start_region_resize(self, event) -> None:
        self._region_resize = (event.x_root, event.y_root, self.region.winfo_width(), self.region.winfo_height())

    def _resize_region(self, event) -> None:
        if not self._region_resize:
            return
        start_x, start_y, start_w, start_h = self._region_resize
        width = max(20, start_w + event.x_root - start_x)
        height = max(20, start_h + event.y_root - start_y)
        self.region.geometry(f"{width}x{height}")
        self._draw_region_frame()
        self.region_data["w"] = width
        self.region_data["h"] = height

    def _finish_region_resize(self, _event) -> None:
        self._region_resize = None

    def _adjust_crosshair_alpha(self, event) -> None:
        self.crosshair_alpha = min(0.95, max(0.15, self.crosshair_alpha + event.delta / 1000))
        self.crosshair.attributes("-alpha", self.crosshair_alpha)

    def _adjust_region_alpha(self, event) -> None:
        self.region_alpha = min(0.9, max(0.15, self.region_alpha + event.delta / 1000))
        self.region.attributes("-alpha", self.region_alpha)

    def _ensure_screenshot_dir(self) -> Path:
        path = Path.cwd() / "screenshot"
        path.mkdir(exist_ok=True)
        return path

    def _screenshot_bbox(self, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
        if not self.hwnd:
            raise RuntimeError("请先绑定窗口")
        left, top = win32gui.ClientToScreen(self.hwnd, (x, y))
        right, bottom = win32gui.ClientToScreen(self.hwnd, (x + w, y + h))
        return left, top, right, bottom

    def _with_hidden_overlays(self, action):
        if not self.available:
            raise RuntimeError("位置工具依赖未安装")
        visible = (self.crosshair.state() != "withdrawn", self.region.state() != "withdrawn")
        self.crosshair.withdraw()
        self.region.withdraw()
        try:
            self.root.update_idletasks()
            time.sleep(0.05)
            return action()
        finally:
            if visible[0]:
                self.crosshair.deiconify()
            if visible[1]:
                self.region.deiconify()

    def capture_full(self) -> Path:
        def action():
            path = self._ensure_screenshot_dir() / time.strftime("%Y-%m-%d_%H-%M-%S.png")
            left, top, right, bottom = self._screenshot_bbox(0, 0, *self._client_size())
            ImageGrab.grab(bbox=(left, top, right, bottom)).save(path)
            return path

        path = self._with_hidden_overlays(action)
        self.on_status(f"已保存窗口截图：{path}")
        return path

    def capture_area(self) -> Path:
        data = self.get_region_data()

        def action():
            suffix = f"({data['x']},{data['y']},{data['w']},{data['h']})"
            path = self._ensure_screenshot_dir() / (time.strftime("%Y-%m-%d_%H-%M-%S") + suffix + ".png")
            bbox = self._screenshot_bbox(data["x"], data["y"], data["w"], data["h"])
            ImageGrab.grab(bbox=bbox).save(path)
            return path

        path = self._with_hidden_overlays(action)
        self.on_status(f"已保存区域截图：{path}")
        return path

    def _client_size(self) -> tuple[int, int]:
        if not self.hwnd:
            raise RuntimeError("请先绑定窗口")
        _left, _top, right, bottom = win32gui.GetClientRect(self.hwnd)
        if right <= 0 or bottom <= 0:
            raise RuntimeError("目标窗口客户区尺寸无效")
        return right, bottom

    def safe_capture(self, mode: str) -> None:
        try:
            self.capture_full() if mode == "full" else self.capture_area()
        except Exception as error:
            self.on_status(f"截图失败：{error}")
            messagebox.showerror("截图失败", str(error), parent=self.root)
