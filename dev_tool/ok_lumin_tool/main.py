from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .capture import CaptureController
from .model import action_label, load_script, normalize_actions, save_script
from .runtime import GlobalShortcut, InputRecorder, PlaybackWorker, ensure_admin, logger


DEFAULT_TARGET_EXE = "EM-Win64-Shipping.exe"
CONFIG_PATH = Path(__file__).with_name("config.json")


def load_default_target_exe() -> str:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            value = json.load(config_file).get("default_target_exe", DEFAULT_TARGET_EXE)
        value = value.strip() if isinstance(value, str) else ""
        return value or DEFAULT_TARGET_EXE
    except (OSError, json.JSONDecodeError, AttributeError):
        return DEFAULT_TARGET_EXE


class UnifiedTool:
    """Tk front end combining target capture tools and action recording."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("OK Lumin 开发工具")
        self.root.geometry("980x720")
        self.root.minsize(820, 580)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.event_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.capture_status = tk.StringVar(value="请绑定目标窗口后再取位置或截图")
        self.action_status = tk.StringVar(value="就绪。录制不保存鼠标坐标，播放使用当前鼠标位置。")
        self._build_style()
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.capture_tab = ttk.Frame(self.notebook, padding=10)
        self.action_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.capture_tab, text="位置与截图")
        self.notebook.add(self.action_tab, text="动作录制与播放")

        self.capture = CaptureController(root, self._set_capture_status)
        self._build_capture_tab()
        self._build_action_tab()
        self._poll_events()
        self._refresh_capture_values()

    @staticmethod
    def _build_style() -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Muted.TLabel", foreground="#606770")

    def _set_capture_status(self, text: str) -> None:
        self.capture_status.set(text)

    def _build_capture_tab(self) -> None:
        tab = self.capture_tab
        ttk.Label(tab, text="位置与截图", style="Title.TLabel").pack(anchor="w")
        ttk.Label(tab, text="十字准心用于取点，区域框用于移动、缩放和截图。浮层可独立显示。",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

        target = ttk.LabelFrame(tab, text="目标窗口", padding=8)
        target.pack(fill="x", pady=(0, 8))
        self.exe_var = tk.StringVar(value=load_default_target_exe())
        ttk.Label(target, text="可执行文件名").grid(row=0, column=0, sticky="w")
        ttk.Entry(target, textvariable=self.exe_var, width=34).grid(row=0, column=1, padx=8, sticky="ew")
        ttk.Button(target, text="绑定窗口", command=self._bind_window).grid(row=0, column=2, padx=(0, 8))
        self.hwnd_label = ttk.Label(target, text="HWND: None", style="Muted.TLabel")
        self.hwnd_label.grid(row=0, column=3, sticky="w")
        target.columnconfigure(1, weight=1)
        ttk.Label(target, text="留空将使用桌面句柄", style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(6, 0))

        point_frame = ttk.LabelFrame(tab, text="十字准心：取位置", padding=8)
        point_frame.pack(fill="x", pady=(0, 8))
        self.point_vars = {name: tk.IntVar(value=0) for name in ("x", "y")}
        self._add_value_fields(point_frame, self.point_vars, ("x", "y"), 0)
        ttk.Button(point_frame, text="从准心读取", command=self._read_crosshair).grid(row=0, column=5, padx=10)
        ttk.Button(point_frame, text="复制位置", command=self.capture.copy_crosshair_position).grid(row=0, column=6)
        self.point_visible = tk.BooleanVar(value=True)
        ttk.Checkbutton(point_frame, text="显示准心", variable=self.point_visible,
                        command=self._toggle_overlays).grid(row=0, column=7, padx=(18, 0))

        region_frame = ttk.LabelFrame(tab, text="区域框：取区域", padding=8)
        region_frame.pack(fill="x", pady=(0, 8))
        self.region_vars = {name: tk.IntVar(value=self.capture.region_data[name]) for name in ("x", "y", "w", "h")}
        self._add_value_fields(region_frame, self.region_vars, ("x", "y", "w", "h"), 0)
        ttk.Button(region_frame, text="应用到区域框", command=self._apply_region).grid(row=0, column=9, padx=10)
        ttk.Button(region_frame, text="读取区域框", command=self._read_region).grid(row=0, column=10)
        self.region_visible = tk.BooleanVar(value=True)
        ttk.Checkbutton(region_frame, text="显示区域框", variable=self.region_visible,
                        command=self._toggle_overlays).grid(row=0, column=11, padx=(18, 0))

        action_frame = ttk.LabelFrame(tab, text="截图", padding=8)
        action_frame.pack(fill="x", pady=(0, 8))
        ttk.Button(action_frame, text="截取整个窗口", command=lambda: self.capture.safe_capture("full")).pack(side="left")
        ttk.Button(action_frame, text="截取当前区域", command=lambda: self.capture.safe_capture("area")).pack(side="left", padx=8)
        ttk.Button(action_frame, text="打开截图文件夹", command=self._open_screenshot_dir).pack(side="left")
        ttk.Button(action_frame, text="更新浮层位置", command=self._apply_all).pack(side="right")

        tips = ttk.LabelFrame(tab, text="操作提示", padding=8)
        tips.pack(fill="both", expand=True)
        tips_text = (
            "十字准心：左键拖动取位置，双击复制坐标，滚轮调整透明度。\n"
            "区域框：左键拖动移动，右键拖动右下角调整大小，左键双击截取窗口，右键双击截取区域。\n"
            "位置坐标和区域坐标均以绑定窗口客户区左上角为原点；截图默认保存到当前目录的 screenshot 文件夹。"
        )
        ttk.Label(tips, text=tips_text, justify="left", style="Muted.TLabel").pack(anchor="nw")
        ttk.Label(tab, textvariable=self.capture_status, anchor="w").pack(fill="x", pady=(8, 0))

    @staticmethod
    def _add_value_fields(parent, variables, names, row: int) -> None:
        column = 0
        for name in names:
            ttk.Label(parent, text=f"{name.upper()}:").grid(row=row, column=column, sticky="w", padx=(0, 3))
            ttk.Entry(parent, textvariable=variables[name], width=8).grid(row=row, column=column + 1, padx=(0, 10))
            column += 2

    def _bind_window(self) -> None:
        try:
            hwnd = self.capture.bind_window(self.exe_var.get())
            self.hwnd_label.configure(text=f"HWND: {hwnd}")
            self._read_region()
            self._read_crosshair()
        except Exception as error:
            self._set_capture_status(f"绑定失败：{error}")
            messagebox.showerror("绑定窗口失败", str(error), parent=self.root)

    def _read_crosshair(self) -> None:
        x, y = self.capture.get_crosshair_position()
        self.point_vars["x"].set(x)
        self.point_vars["y"].set(y)

    def _read_region(self) -> None:
        data = self.capture.get_region_data()
        for name, value in data.items():
            self.region_vars[name].set(value)

    def _apply_region(self) -> None:
        try:
            data = {name: max(1, int(var.get())) for name, var in self.region_vars.items()}
            self.capture.apply_region_data(data)
            self._set_capture_status("已应用区域框坐标")
        except (TypeError, tk.TclError, ValueError) as error:
            messagebox.showerror("坐标无效", str(error), parent=self.root)

    def _apply_all(self) -> None:
        self._apply_region()
        self._set_capture_status("已更新浮层位置")

    def _toggle_overlays(self) -> None:
        self.capture.set_visible(self.point_visible.get())
        self.capture.set_region_visible(self.region_visible.get())

    def _refresh_capture_values(self) -> None:
        if not self.root.winfo_exists():
            return
        if self.capture.crosshair.winfo_exists() and self.capture.region.winfo_exists():
            self._read_crosshair()
            self._read_region()
        self.root.after(350, self._refresh_capture_values)

    def _open_screenshot_dir(self) -> None:
        path = Path.cwd() / "screenshot"
        path.mkdir(exist_ok=True)
        os.startfile(path)

    def _build_action_tab(self) -> None:
        tab = self.action_tab
        ttk.Label(tab, text="动作录制与播放", style="Title.TLabel").pack(anchor="w")
        ttk.Label(tab, text="记录键盘与鼠标按下/抬起事件；不记录鼠标移动或坐标。",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="模式").pack(side="left")
        self.mode_var = tk.StringVar(value="录制")
        self.mode_box = ttk.Combobox(controls, textvariable=self.mode_var, state="readonly", width=10,
                                     values=("录制", "播放"))
        self.mode_box.set("录制")
        self.mode_box.pack(side="left", padx=(8, 18))
        self.mode_box.bind("<<ComboboxSelected>>", self._mode_changed)
        self.start_button = ttk.Button(controls, text="F8 开始录制", command=self._start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(controls, text="F9 暂停", command=self._pause_resume, state="disabled")
        self.pause_button.pack(side="left", padx=6)
        self.stop_button = ttk.Button(controls, text="F10 终止", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")
        self.open_button = ttk.Button(controls, text="打开脚本", command=self._open_script)
        self.open_button.pack(side="right")
        self.save_button = ttk.Button(controls, text="导出脚本", command=self._save_script)
        self.save_button.pack(side="right", padx=6)

        shortcut_frame = ttk.Frame(tab)
        shortcut_frame.pack(fill="x", pady=(0, 8))
        self.shortcut_button = ttk.Button(shortcut_frame, text="启用全局 F8 / F9 / F10",
                                          command=self._toggle_shortcuts)
        self.shortcut_button.pack(side="left")
        ttk.Label(shortcut_frame, text="全局快捷键仅在 Windows 上可用；录制时会自动忽略 F8/F9/F10。",
                  style="Muted.TLabel").pack(side="left", padx=10)

        table_frame = ttk.Frame(tab)
        table_frame.pack(fill="both", expand=True)
        columns = ("timestamp", "action", "value")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.table.heading("timestamp", text="时间戳(ns)")
        self.table.heading("action", text="操作")
        self.table.heading("value", text="按键/鼠标键")
        self.table.column("timestamp", width=220, anchor="e")
        self.table.column("action", width=260)
        self.table.column("value", width=180)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(tab, textvariable=self.action_status, anchor="w").pack(fill="x", pady=(8, 0))

        self.actions: list[dict] = []
        self.path: Path | None = None
        self.recorder = InputRecorder()
        self.playback: PlaybackWorker | None = None
        self.hotkey = GlobalShortcut(lambda hotkey_id: self.event_queue.put(("hotkey", hotkey_id)))
        self.shortcut_active = False
        self._register_shortcuts()

    def _mode_changed(self, _event=None) -> None:
        if self.start_button["state"] == "disabled":
            self.mode_box.set(self.mode_var.get())
            return
        player = self.mode_var.get() == "播放"
        self.start_button.configure(text="F8 开始播放" if player else "F8 开始录制")

    def _start(self) -> None:
        if self.mode_var.get() == "录制":
            try:
                self.recorder.start()
            except Exception as error:
                logger.exception("recorder start failed")
                messagebox.showerror("录制器无法启动", str(error), parent=self.root)
                return
            self.actions = []
            digest = hashlib.sha256(str(time.time_ns()).encode("ascii")).hexdigest()[:8]
            self.path = Path(f"steps-{digest}.stepscript")
            self._clear_table()
            self.action_status.set("正在录制全局键盘和鼠标按键。")
        else:
            if not self.actions:
                messagebox.showinfo("没有可播放的动作", "请先打开或录制一个 .stepscript 文件。", parent=self.root)
                return
            self.actions = normalize_actions(self.actions)
            self._populate_table()
            self.playback = PlaybackWorker(
                self.actions,
                lambda action: self.event_queue.put(("playback", action)),
                lambda: self.event_queue.put(("finished", None)),
            )
            self.playback.start()
            self.action_status.set("开始播放，鼠标点击会在当前指针位置执行。")
        self.start_button.configure(state="disabled")
        self.mode_box.configure(state="disabled")
        self.pause_button.configure(state="normal")
        self.stop_button.configure(state="normal")

    def _pause_resume(self) -> None:
        if self.mode_var.get() == "录制":
            if self.recorder.paused:
                self.recorder.resume()
                self.pause_button.configure(text="F9 暂停")
                self.action_status.set("正在录制。")
            else:
                self.recorder.pause()
                self.pause_button.configure(text="F9 继续")
                self.action_status.set("录制已暂停。")
        elif self.playback:
            if self.playback.resume_event.is_set():
                self.playback.pause()
                self.pause_button.configure(text="F9 继续")
                self.action_status.set("播放已暂停。")
            else:
                self.playback.resume()
                self.pause_button.configure(text="F9 暂停")
                self.action_status.set("播放已继续。")

    def _stop(self) -> None:
        if self.mode_var.get() == "录制" and self.recorder.recording:
            self.actions = normalize_actions(self.recorder.stop())
            self._populate_table()
            self.action_status.set(f"录制已终止，共 {len(self.actions)} 个动作。")
        if self.playback and self.playback.is_alive():
            self.playback.stop()
        self._set_idle()

    def _set_idle(self) -> None:
        self.start_button.configure(state="normal")
        self.mode_box.configure(state="readonly")
        self.pause_button.configure(state="disabled", text="F9 暂停")
        self.stop_button.configure(state="disabled")

    def _register_shortcuts(self) -> None:
        try:
            self.hotkey.start()
            self.shortcut_active = True
            self.shortcut_button.configure(text="全局快捷键已启用：F8 / F9 / F10")
        except Exception as error:
            logger.info("global shortcuts unavailable: %s", error)
            self.shortcut_active = False
            self.action_status.set(f"全局快捷键不可用：{error}")

    def _toggle_shortcuts(self) -> None:
        if self.shortcut_active:
            self.hotkey.stop()
            self.shortcut_active = False
            self.shortcut_button.configure(text="启用全局 F8 / F9 / F10")
        else:
            self._register_shortcuts()

    def _handle_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == 1:
            if self.start_button["state"] != "disabled":
                self._start()
            elif self.pause_button["state"] != "disabled" and (
                (self.mode_var.get() == "录制" and self.recorder.paused)
                or (self.mode_var.get() == "播放" and self.playback and not self.playback.resume_event.is_set())
            ):
                self._pause_resume()
        elif hotkey_id == 2 and self.pause_button["state"] != "disabled":
            self._pause_resume()
        elif hotkey_id == 3 and self.stop_button["state"] != "disabled":
            self._stop()

    def _drain_recorded_actions(self) -> None:
        batch = self.recorder.drain_pending()
        if not batch:
            return
        for action in batch:
            self.actions.append(action)
            self._render_action(action)
        self.table.yview_moveto(1)

    def _poll_events(self) -> None:
        self._drain_recorded_actions()
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                if event_type == "hotkey":
                    self._handle_hotkey(int(payload))
                elif event_type == "playback":
                    self._select_action(payload)
                elif event_type == "finished":
                    self._set_idle()
                    error = getattr(self.playback, "error", None)
                    self.action_status.set(f"播放失败：{error}" if error else "播放已结束，按下的按键已释放。")
        except queue.Empty:
            pass
        self.root.after(40, self._poll_events)

    def _render_action(self, action: dict) -> None:
        value = action.get("key", action.get("button", ""))
        self.table.insert("", "end", values=(action["timestamp_ns"], action_label(action), value))

    def _clear_table(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)

    def _populate_table(self) -> None:
        self._clear_table()
        for action in self.actions:
            self._render_action(action)
        children = self.table.get_children()
        if children:
            self.table.selection_set(children[0])

    def _select_action(self, action: dict) -> None:
        for index, candidate in enumerate(self.actions):
            if candidate is action:
                children = self.table.get_children()
                if index < len(children):
                    self.table.selection_set(children[index])
                    self.table.see(children[index])
                return

    def _open_script(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="打开动作脚本", filetypes=(("步骤脚本", "*.stepscript"), ("所有文件", "*.*"))
        )
        if not path:
            return
        try:
            self.actions = load_script(path)
            self.path = Path(path)
            self._populate_table()
            self.action_status.set(f"已载入 {self.path.name}，共 {len(self.actions)} 个动作。")
        except Exception as error:
            logger.exception("opening stepscript failed")
            messagebox.showerror("无法打开脚本", str(error), parent=self.root)

    def _save_script(self) -> None:
        initial = str(self.path or "steps.stepscript")
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出动作脚本",
            initialfile=Path(initial).name,
            defaultextension=".stepscript",
            filetypes=(("步骤脚本", "*.stepscript"),),
        )
        if not path:
            return
        try:
            save_script(path, self.actions)
            self.path = Path(path)
            self.action_status.set(f"已导出 {len(self.actions)} 个动作至 {self.path.name}。")
        except Exception as error:
            logger.exception("exporting stepscript failed")
            messagebox.showerror("无法导出脚本", str(error), parent=self.root)

    def close(self) -> None:
        if self.recorder.recording:
            self.recorder.stop()
        if self.playback and self.playback.is_alive():
            self.playback.stop()
            self.playback.join(timeout=1)
        self.hotkey.stop()
        self.capture.destroy()
        self.root.destroy()


def set_dpi_aware() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def launch() -> int:
    ensure_admin()
    set_dpi_aware()
    root = tk.Tk()
    UnifiedTool(root)
    root.mainloop()
    return 0


def main() -> int:
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
