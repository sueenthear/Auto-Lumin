"""Optional native WASAPI check: only the bound process tree is captured.

Real audio playback is required, so this test is skipped unless
AUDIO_NATIVE_TEST=1 is set. It never touches a game window.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

import numpy as np

from script_core.audio_manager.loopback import SAMPLE_RATE, ProcessLoopback, check_supported

ENABLED = os.environ.get("AUDIO_NATIVE_TEST") == "1"


def write_tone(path: Path, frequency: int) -> None:
    t = np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE
    samples = (11000 * np.sin(2 * np.pi * frequency * t)).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples)


@unittest.skipUnless(ENABLED and os.name == "nt", "set AUDIO_NATIVE_TEST=1 on Windows to run")
class NativeProcessLoopbackTests(unittest.TestCase):
    def test_captures_target_process_and_excludes_another_process(self):
        check_supported()
        with tempfile.TemporaryDirectory() as directory:
            target_sound = Path(directory) / "target.wav"
            other_sound = Path(directory) / "other.wav"
            write_tone(target_sound, 500)
            write_tone(other_sound, 1200)
            player = (
                "import sys,time,winsound; time.sleep(0.6); "
                "winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME)"
            )
            with subprocess.Popen([sys.executable, "-c", player, str(target_sound)]) as target:
                with subprocess.Popen([sys.executable, "-c", player, str(other_sound)]) as other:
                    chunks: list[bytes] = []
                    errors: list[BaseException] = []

                    def collect() -> None:
                        # Capture on a worker thread: COM must be opened and closed there.
                        try:
                            with ProcessLoopback(target.pid) as capture:
                                deadline = time.monotonic() + 3.2
                                while time.monotonic() < deadline:
                                    chunks.append(capture.read())
                        except BaseException as exc:  # surfaced below
                            errors.append(exc)

                    worker = threading.Thread(target=collect, name="NativeLoopbackProbe")
                    worker.start()
                    worker.join(timeout=12)
                    self.assertFalse(worker.is_alive(), "capture did not stop")
                    if errors:
                        raise errors[0]
                    target.wait(timeout=5)
                    other.wait(timeout=5)

            data = np.frombuffer(b"".join(chunks), dtype="<i2").astype(float)
            self.assertGreater(len(data), SAMPLE_RATE, "no audio captured for the target process")
            spectrum = np.abs(np.fft.rfft(data[:SAMPLE_RATE]))
            self.assertGreater(
                spectrum[500],
                100 * max(spectrum[1200], 1),
                "captured audio from another process",
            )


if __name__ == "__main__":
    unittest.main()
