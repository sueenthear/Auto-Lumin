"""Load and match .voice templates (RIFF/WAV, mono signed PCM16, 16 kHz)."""
from __future__ import annotations

import io
import math
import wave
from pathlib import Path

import numpy as np

from .loopback import CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH

MAX_SECONDS = 10
MIN_SECONDS = 0.04


def decode_voice(data: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(data), "rb") as sound:
            if (sound.getnchannels(), sound.getsampwidth(), sound.getframerate(), sound.getcomptype()) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE, "NONE"):
                raise ValueError(".voice 必须为 16 kHz / mono / 16-bit PCM WAV")
            count = sound.getnframes()
            if not MIN_SECONDS * SAMPLE_RATE <= count <= MAX_SECONDS * SAMPLE_RATE:
                raise ValueError(f"模板长度应为 {MIN_SECONDS:g} 至 {MAX_SECONDS:g} 秒")
            raw = sound.readframes(count)
            if len(raw) != count * CHANNELS * SAMPLE_WIDTH:
                raise ValueError(".voice WAV 音频帧数据不完整")
            samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    except (EOFError, wave.Error) as exc:
        raise ValueError("无效的 .voice WAV 模板") from exc
    if not np.max(np.abs(samples)) > 96:
        raise ValueError("不能用静音模板进行匹配；请录制真实的目标音效")
    return samples


def load_voice(path: str | Path) -> np.ndarray:
    return decode_voice(Path(path).read_bytes())


def encode_voice(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % (CHANNELS * SAMPLE_WIDTH):
        raise ValueError("录音必须为非空且完整的 PCM 帧")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as sound:
        sound.setnchannels(CHANNELS)
        sound.setsampwidth(SAMPLE_WIDTH)
        sound.setframerate(SAMPLE_RATE)
        sound.writeframes(pcm)
    return stream.getvalue()


def save_voice(path: str | Path, pcm: bytes) -> Path:
    destination = Path(path)
    if destination.suffix.lower() != ".voice":
        raise ValueError("录音文件扩展名必须为 .voice")
    destination.write_bytes(encode_voice(pcm))
    return destination


def trim_voice_samples(samples: np.ndarray, start_seconds: float, end_seconds: float) -> bytes:
    """Return a validated PCM16 clip from decoded .voice samples."""
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
        raise ValueError("剪辑起止时间必须是有效数字")
    start = round(start_seconds * SAMPLE_RATE)
    end = round(end_seconds * SAMPLE_RATE)
    if start < 0 or end <= start or end > len(samples):
        raise ValueError("剪辑范围必须位于音频时长内，且结束时间晚于开始时间")
    if end - start < MIN_SECONDS * SAMPLE_RATE:
        raise ValueError(f"剪辑长度不能短于 {MIN_SECONDS:g} 秒")
    return np.asarray(samples[start:end], dtype="<i2").tobytes()


def validate_threshold(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError("匹配阈值必须在 (0, 1] 之间")
    return float(value)


class VoiceMatcher:
    """Incremental scale-independent normalized cross-correlation.

    Scores are in [0, 1], with silence ignored. The buffer is capped at
    the template length plus the incoming packet size.
    """

    def __init__(self, samples: np.ndarray, threshold: float = 0.8):
        self.template = np.asarray(samples, dtype=np.float32).copy()
        if self.template.ndim != 1 or self.template.size < 1:
            raise ValueError("模板不能为空")
        self._mean = float(self.template.mean())
        self.template -= self._mean
        self._energy = float(np.dot(self.template, self.template))
        if self._energy < 96.0 ** 2:
            raise ValueError("模板音频变化过小，无法匹配")
        self._buffer = np.empty(0, dtype=np.float32)
        self._threshold = 0.8
        self.threshold = threshold
        self.armed = True
        self._quiet_frames = 0
        self._unmatched_frames = 0

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= 1:
            raise ValueError("匹配阈值必须在 (0, 1] 之间")
        self._threshold = float(value)

    def feed(self, pcm: bytes) -> tuple[float, bool]:
        """Return (maximum similarity, just-triggered)."""
        if len(pcm) % SAMPLE_WIDTH:
            raise ValueError("收到不完整的 PCM 帧")
        if not pcm:
            return 0.0, False
        fresh = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        n = self.template.size
        signal = np.concatenate((self._buffer, fresh))
        self._buffer = signal[-(n - 1):] if n > 1 else np.empty(0, dtype=np.float32)
        if signal.size < n:
            return 0.0, False
        size = 1 << (signal.size + n - 2).bit_length()
        correlation = np.fft.irfft(
            np.fft.rfft(signal, size) * np.fft.rfft(self.template[::-1], size), size
        )[n - 1:signal.size]
        prefix = np.concatenate(([0.0], np.cumsum(signal.astype(np.float64))))
        squared = np.concatenate(([0.0], np.cumsum(signal.astype(np.float64) ** 2)))
        sums = prefix[n:] - prefix[:-n]
        energy = np.maximum(0.0, squared[n:] - squared[:-n] - sums * sums / n)
        # Prevent a tiny background hiss from matching a quiet portion of a louder template.
        signal_rms = np.sqrt(energy / n)
        scores = np.where(
            signal_rms > 96,
            np.maximum(0, correlation) / np.sqrt(np.maximum(energy * self._energy, 1)),
            0.0,
        )
        score = float(np.clip(scores.max(), 0, 1))
        triggered = score >= self.threshold and self.armed
        # A short dip between chunks of the same sound must not count as a
        # second event. Silence resets quickly; other sounds require a full
        # template-length interval without a matching newest window.
        if np.max(np.abs(fresh)) <= 96:
            self._quiet_frames += len(fresh)
        else:
            self._quiet_frames = 0
        if scores[-1] < self.threshold:
            self._unmatched_frames += len(fresh)
        else:
            self._unmatched_frames = 0
        if triggered:
            self.armed = False
            self._quiet_frames = self._unmatched_frames = 0
        elif self._quiet_frames >= SAMPLE_RATE // 25 or self._unmatched_frames >= n:
            self.armed = True
        return score, triggered
