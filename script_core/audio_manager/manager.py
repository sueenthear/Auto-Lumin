"""Bound-window process audio matching and bounded VOICE recording."""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Callable

from .loopback import ProcessLoopback, SAMPLE_RATE, SAMPLE_WIDTH, check_supported
from .template import MAX_SECONDS, VoiceMatcher, load_voice, save_voice


def default_template() -> Path:
    """Return the template.voice bundled with the package.

    Only the packaged template is used: a stray file in the working directory
    must not silently change which sound is matched.
    """
    bundled = Path(__file__).with_name("template.voice")
    if not bundled.is_file():
        raise FileNotFoundError(
            "包内缺少 template.voice；请用录音工具导出目标音效并覆盖 "
            f"{bundled}，或传给 do.audio.listen.start('路径.voice')"
        )
    return bundled


class AudioManager:
    """Capture and match audio solely from the process bound to a window."""

    def __init__(self, window, capture_factory=ProcessLoopback):
        self.window = window
        self.capture_factory = capture_factory
        self._action: Callable[[], None] | None = None
        self._threshold = 0.8
        self._matcher: VoiceMatcher | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._action_worker: threading.Thread | None = None
        self.error: BaseException | None = None
        self.last_score = 0.0

    def set_action(self, func: Callable[[], None]) -> None:
        if not callable(func):
            raise TypeError("audio action must be a callable with no arguments")
        with self._lock:
            self._action = func

    def set_threshold(self, value: float) -> None:
        # Validation is shared with the matcher even when listening has not started.
        from .template import validate_threshold

        validated = validate_threshold(value)
        with self._lock:
            self._threshold = validated
            if self._matcher is not None:
                self._matcher.threshold = validated

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def listening(self) -> bool:
        return self._worker is not None and self._worker.is_alive() and self._ready.is_set()

    def _target_pid(self) -> int:
        if not self.window.alive:
            raise RuntimeError("绑定窗口已失效；无法监听其音频进程")
        pid = self.window.process_id
        if not isinstance(pid, int) or pid <= 0:
            raise RuntimeError("无法获取绑定窗口对应的进程 PID")
        return pid

    def start(self, template: str | Path | None = None) -> None:
        source = default_template() if template is None else Path(template)
        samples = load_voice(source)
        check_supported()
        pid = self._target_pid()
        with self._lifecycle_lock:
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("音频监听已经在运行")
            if self._action_worker is not None and self._action_worker.is_alive():
                raise RuntimeError("上一音频动作尚未结束")
            self._matcher = VoiceMatcher(samples, self._threshold)
            self._stop.clear()
            self._ready.clear()
            self.error = None
            self.last_score = 0.0
            self._worker = threading.Thread(target=self._listen, args=(pid,), name="ProcessAudioListen", daemon=True)
            self._worker.start()
        if not self._ready.wait(12):
            self.stop()
            raise TimeoutError("音频监听启动超时")
        if self.error is not None or self._stop.is_set():
            cause = self.error
            self.stop()
            raise RuntimeError("绑定进程音频监听失败或已停止") from cause

    def _listen(self, pid: int) -> None:
        try:
            with self.capture_factory(pid) as capture:
                self._ready.set()
                while not self._stop.is_set():
                    if not self.window.alive or self.window.process_id != pid:
                        raise RuntimeError("绑定窗口进程已退出或发生变更，监听自动终止")
                    pcm = capture.read()
                    if self._stop.is_set():
                        break
                    if not pcm:
                        # No rendered samples for this period: advance by the read timeout.
                        pcm = bytes(SAMPLE_RATE * SAMPLE_WIDTH // 10)
                    # Bound each match pass even if one read contains multiple packets.
                    step = SAMPLE_RATE * SAMPLE_WIDTH // 20
                    for offset in range(0, len(pcm), step):
                        if self._stop.is_set():
                            break
                        with self._lock:
                            score, triggered = self._matcher.feed(pcm[offset:offset + step])
                            self.last_score = score
                            action = self._action if triggered else None
                            busy = self._action_worker is not None and self._action_worker.is_alive()
                            if action and not busy and not self._stop.is_set():
                                self._action_worker = threading.Thread(
                                    target=self._invoke_action, args=(action,), name="ProcessAudioAction", daemon=True
                                )
                                self._action_worker.start()
        except BaseException as exc:
            self.error = exc
            self._ready.set()
        finally:
            self._stop.set()

    def _invoke_action(self, action: Callable[[], None]) -> None:
        try:
            action()
        except BaseException as exc:
            self.error = exc
            self._stop.set()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            worker = self._worker
            if worker and worker is not threading.current_thread():
                worker.join(timeout=15)
            if worker and worker.is_alive():
                raise TimeoutError("音频监听线程未能停止")
            self._worker = None
            self._ready.clear()
            action = self._action_worker
            if action and action is not threading.current_thread():
                action.join(timeout=15)
            if action and action.is_alive() and action is not threading.current_thread():
                raise TimeoutError("音频动作线程未能停止")
            if action is not threading.current_thread():
                self._action_worker = None

    def record(
        self,
        path: str | Path,
        seconds: float,
        progress: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> Path:
        """Record bound-process audio until the limit or an optional stop event."""
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
            raise ValueError(f"录音时长必须在 0 到 {MAX_SECONDS} 秒之间")
        check_supported()
        pid = self._target_pid()
        chunks: list[bytes] = []
        captured = 0
        required = round(seconds * SAMPLE_RATE * SAMPLE_WIDTH)
        required -= required % SAMPLE_WIDTH
        with self.capture_factory(pid) as capture:
            deadline = time.monotonic() + seconds + 2
            while captured < required and not (stop_event and stop_event.is_set()):
                if not self.window.alive or self.window.process_id != pid:
                    raise RuntimeError("绑定窗口进程已退出或发生变更，录音已取消")
                if time.monotonic() > deadline:
                    raise TimeoutError("目标进程没有及时提供音频数据")
                packet = capture.read()
                if not packet:
                    continue
                needed = required - captured
                packet = packet[:needed]
                chunks.append(packet)
                captured += len(packet)
                if progress:
                    progress(captured / (SAMPLE_RATE * SAMPLE_WIDTH))
        pcm = b"".join(chunks)
        # Empty recordings are not useful for matching and should not overwrite files.
        from .template import decode_voice, encode_voice
        decode_voice(encode_voice(pcm))
        return save_voice(path, pcm)
