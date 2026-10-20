"""Windows GUI for recording, auditioning and trimming process-scoped .voice files."""
from __future__ import annotations

import io
import queue
import threading
import tkinter as tk
import wave
import winsound
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pynput.keyboard import GlobalHotKeys

from script_core.audio_manager import AudioManager, check_supported, load_voice, save_voice, trim_voice_samples
from script_core.audio_manager.loopback import SAMPLE_RATE, SAMPLE_WIDTH
from script_core.window_manager import WindowManager


class VoiceRecorder:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("进程音频录制与剪辑 (.voice)")
        self.root.geometry("700x410")
        self.root.minsize(660, 390)
        self.exe = tk.StringVar(value="Lumin.exe")
        self.duration = tk.StringVar(value="10.0")
        self.output = tk.StringVar(value=str(Path(__file__).resolve().parent.parent / "script_core" / "audio_manager" / "template.voice"))
        self.source = tk.StringVar(value="")
        self.start_time = tk.StringVar(value="0.00")
        self.end_time = tk.StringVar(value="0.00")
        self.status = tk.StringVar(value="绑定窗口后按 F8 开始录制，按 F9 结束并保存。")
        self.manager: AudioManager | None = None
        self.samples = None
        self._worker: threading.Thread | None = None
        self._record_stop: threading.Event | None = None
        self._is_recording = False
        self._closed = False
        self._hotkey_commands: queue.Queue[str] = queue.Queue()
        self._hotkeys: GlobalHotKeys | None = None
        self._playing = False
        self._build()
        self._start_hotkeys()
        self.root.after(100, self._poll_hotkeys)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self) -> None:
        form = ttk.Frame(self.root, padding=14)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="目标 exe").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.exe).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(form, text="绑定", command=self.bind).grid(row=0, column=2)

        ttk.Label(form, text="最长录制秒数 (0.04-10)").grid(row=1, column=0, sticky="w", pady=10)
        ttk.Entry(form, textvariable=self.duration, width=12).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(form, text="录制输出").grid(row=2, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.output).grid(row=2, column=1, sticky="ew", padx=8)
        ttk.Button(form, text="选择文件", command=self.choose_output).grid(row=2, column=2)
        record_controls = ttk.Frame(form)
        record_controls.grid(row=3, column=1, sticky="w", padx=8, pady=(8, 14))
        self.record_button = ttk.Button(record_controls, text="开始录制 (F8)", command=self.record)
        self.record_button.pack(side="left")
        self.stop_button = ttk.Button(record_controls, text="结束并保存 (F9)", command=self.stop_recording, state="disabled")
        self.stop_button.pack(side="left", padx=8)

        ttk.Separator(form).grid(row=4, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(form, text="编辑源文件").grid(row=5, column=0, sticky="w", pady=8)
        ttk.Entry(form, textvariable=self.source, state="readonly").grid(row=5, column=1, sticky="ew", padx=8)
        ttk.Button(form, text="打开 .voice", command=self.open_source).grid(row=5, column=2)

        edit = ttk.Frame(form)
        edit.grid(row=6, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(edit, text="起始 (秒)").pack(side="left")
        ttk.Entry(edit, textvariable=self.start_time, width=9).pack(side="left", padx=(6, 18))
        ttk.Label(edit, text="结束 (秒)").pack(side="left")
        ttk.Entry(edit, textvariable=self.end_time, width=9).pack(side="left", padx=6)
        ttk.Button(edit, text="试听剪辑", command=self.preview_clip).pack(side="left", padx=(12, 5))
        ttk.Button(edit, text="试听停止", command=self.stop_preview).pack(side="left", padx=5)

        save_row = ttk.Frame(form)
        save_row.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 12))
        ttk.Button(save_row, text="试听整段", command=self.preview_full).pack(side="left")
        ttk.Button(save_row, text="保存剪辑为 .voice", command=self.save_clip).pack(side="left", padx=8)
        ttk.Label(form, textvariable=self.status, wraplength=650).grid(row=8, column=0, columnspan=3, sticky="w")
        form.columnconfigure(1, weight=1)

    def _start_hotkeys(self) -> None:
        self._hotkeys = GlobalHotKeys({
            "<f8>": lambda: self._hotkey_commands.put("start"),
            "<f9>": lambda: self._hotkey_commands.put("stop"),
        })
        self._hotkeys.start()

    def _poll_hotkeys(self) -> None:
        if self._closed:
            return
        try:
            while True:
                command = self._hotkey_commands.get_nowait()
                if command == "start":
                    self.record()
                else:
                    self.stop_recording()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_hotkeys)

    def bind(self) -> None:
        try:
            check_supported()
            name = self.exe.get().strip()
            if not name or not name.lower().endswith(".exe"):
                raise ValueError("请填写具体可执行文件名，例如 Lumin.exe")
            window = WindowManager(executable_name=name)
            self.manager = AudioManager(window)
            self.status.set(f"已绑定 {name} (PID {window.process_id})")
        except Exception as exc:
            self.manager = None
            messagebox.showerror("绑定失败", str(exc), parent=self.root)

    def choose_output(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self.root, title="保存进程录音", defaultextension=".voice",
            filetypes=(("VOICE 音频", "*.voice"),), initialfile="template.voice",
        )
        if filename:
            self.output.set(filename)

    def record(self) -> None:
        if self._is_recording:
            self.status.set("录制正在进行；按 F9 结束并保存。")
            return
        try:
            if self.manager is None:
                raise RuntimeError("请先绑定目标 exe")
            duration = float(self.duration.get())
            if not 0.04 <= duration <= 10:
                raise ValueError("最长录制时长必须在 0.04 到 10 秒之间")
            path = Path(self.output.get())
            if path.suffix.lower() != ".voice":
                raise ValueError("输出文件扩展名必须为 .voice")
        except (ValueError, RuntimeError) as exc:
            self.status.set(str(exc))
            if self.manager is None:
                messagebox.showerror("无法录制", str(exc), parent=self.root)
            return

        stop_event = threading.Event()
        self._record_stop = stop_event
        self._is_recording = True
        self.record_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("录制中：按 F9 结束并保存，最长录制 " + f"{duration:g} 秒。")

        def work() -> None:
            try:
                result = self.manager.record(path, duration, stop_event=stop_event)
                if not self._closed:
                    self.root.after(0, lambda result=result: self._recorded(result))
            except Exception as exc:
                if not self._closed:
                    self.root.after(0, lambda exc=exc: self._record_failed(exc))

        self._worker = threading.Thread(target=work, name="VoiceRecorder", daemon=True)
        self._worker.start()

    def stop_recording(self) -> None:
        if not self._is_recording or self._record_stop is None:
            return
        self._record_stop.set()
        self.stop_button.configure(state="disabled")
        self.status.set("正在结束录制并保存文件...")

    def _recorded(self, path: Path) -> None:
        self._set_recording_finished()
        try:
            self._load_source(path)
            self.status.set(f"录制完成并载入编辑区：{path}")
        except Exception as exc:
            self._record_failed(exc)

    def _record_failed(self, exc: Exception) -> None:
        self._set_recording_finished()
        self.status.set(f"录制失败或不可用：{exc}")

    def _set_recording_finished(self) -> None:
        self._is_recording = False
        self._record_stop = None
        self.record_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def open_source(self) -> None:
        filename = filedialog.askopenfilename(
            parent=self.root, title="打开 .voice 音频", filetypes=(("VOICE 音频", "*.voice"),),
        )
        if filename:
            try:
                self._load_source(Path(filename))
                self.status.set(f"已载入 {filename}，设置起止时间后试听或保存剪辑。")
            except Exception as exc:
                messagebox.showerror("无法打开", str(exc), parent=self.root)

    def _load_source(self, path: Path) -> None:
        samples = load_voice(path)
        self.samples = samples
        self.source.set(str(path))
        self.start_time.set("0.00")
        self.end_time.set(f"{len(samples) / SAMPLE_RATE:.2f}")

    def _clip_pcm(self) -> bytes:
        if self.samples is None:
            raise RuntimeError("请先录制或打开一个 .voice 文件")
        return trim_voice_samples(self.samples, float(self.start_time.get()), float(self.end_time.get()))

    @staticmethod
    def _wav_bytes(pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm)
        return output.getvalue()

    def preview_full(self) -> None:
        if self.samples is None:
            messagebox.showerror("无法试听", "请先录制或打开一个 .voice 文件", parent=self.root)
            return
        self._play(self._wav_bytes(self.samples.astype("<i2").tobytes()))

    def preview_clip(self) -> None:
        try:
            self._play(self._wav_bytes(self._clip_pcm()))
        except Exception as exc:
            messagebox.showerror("无法试听剪辑", str(exc), parent=self.root)

    def _play(self, audio: bytes) -> None:
        self.stop_preview()
        self._playing = True

        def playback() -> None:
            try:
                winsound.PlaySound(audio, winsound.SND_MEMORY | winsound.SND_SYNC)
            finally:
                self._playing = False

        threading.Thread(target=playback, name="VoicePreview", daemon=True).start()
        self.status.set("正在试听音频...")

    def stop_preview(self) -> None:
        if self._playing:
            winsound.PlaySound(None, winsound.SND_PURGE)
            self._playing = False

    def save_clip(self) -> None:
        try:
            pcm = self._clip_pcm()
            filename = filedialog.asksaveasfilename(
                parent=self.root, title="保存剪辑后的模板", defaultextension=".voice",
                filetypes=(("VOICE 音频", "*.voice"),), initialfile="template-clipped.voice",
            )
            if not filename:
                return
            result = save_voice(filename, pcm)
            self.output.set(str(result))
            self.status.set(f"剪辑已保存：{result}")
        except Exception as exc:
            messagebox.showerror("无法保存剪辑", str(exc), parent=self.root)

    def close(self) -> None:
        self._closed = True
        if self._record_stop is not None:
            self._record_stop.set()
        if self._hotkeys is not None:
            self._hotkeys.stop()
        self.stop_preview()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    VoiceRecorder(root)
    root.mainloop()


if __name__ == "__main__":
    main()
