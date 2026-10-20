import math
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from script_core.audio_manager import AudioManager, VoiceMatcher, load_voice, save_voice, trim_voice_samples
from script_core.audio_manager.template import decode_voice
from script_core.facade import _Audio


def tone(n=1600):
    # Multi-frequency signal with time-dependent envelope avoids accidental matches.
    t = np.arange(n) / 16000
    return (12000 * np.sin(2 * np.pi * (390 * t + 35 * t * t)) * (0.35 + t)).astype('<i2').tobytes()


class _Capture:
    def __init__(self, pid, packets):
        self.pid = pid
        self.packets = list(packets)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        if self.packets:
            return self.packets.pop(0)
        time.sleep(0.01)
        return b''


class AudioTests(unittest.TestCase):
    def test_voice_roundtrip_and_silence_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'template.voice'
            save_voice(path, tone())
            self.assertTrue(path.read_bytes().startswith(b'RIFF'))
            np.testing.assert_array_equal(load_voice(path), np.frombuffer(tone(), dtype='<i2'))
            with self.assertRaisesRegex(ValueError, '静音'):
                decode_voice(save_bytes := self._silent_voice(directory))

    def _silent_voice(self, directory):
        path = Path(directory) / 'silent.voice'
        save_voice(path, bytes(1600 * 2))
        return path.read_bytes()

    def test_voice_trim_preserves_selected_samples_and_validates_range(self):
        samples = np.frombuffer(tone(3200), dtype='<i2')
        clip = trim_voice_samples(samples, 0.05, 0.15)
        np.testing.assert_array_equal(
            np.frombuffer(clip, dtype='<i2'), samples[800:2400]
        )
        with self.assertRaisesRegex(ValueError, '范围'):
            trim_voice_samples(samples, 0.15, 0.05)
        with self.assertRaisesRegex(ValueError, '短于'):
            trim_voice_samples(samples, 0.0, 0.02)
        with self.assertRaisesRegex(ValueError, '范围'):
            trim_voice_samples(samples, 0.0, 0.3)

    def test_matcher_spanning_packets_and_rearms_after_silence(self):
        sample = tone()
        matcher = VoiceMatcher(np.frombuffer(sample, dtype='<i2').astype(np.float32), 0.8)
        self.assertFalse(matcher.feed(sample[:500])[1])
        score, triggered = matcher.feed(sample[500:])
        self.assertGreater(score, 0.8)
        self.assertTrue(triggered)
        self.assertFalse(matcher.feed(sample)[1])
        self.assertFalse(matcher.feed(bytes(len(sample)))[1])
        self.assertTrue(matcher.feed(sample)[1])

    def test_manager_receives_bound_pid_and_calls_action_once(self):
        window = SimpleNamespace(alive=True, process_id=421)
        captured = []
        callbacks = []
        sample = tone()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target.voice'
            save_voice(path, sample)
            def factory(pid):
                captured.append(pid)
                return _Capture(pid, [sample, sample, bytes(len(sample)), sample])
            manager = AudioManager(window, factory)
            manager.set_action(lambda: callbacks.append(1))
            with patch('script_core.audio_manager.manager.check_supported'):
                manager.start(path)
                deadline = time.monotonic() + 2
                while len(callbacks) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                manager.stop()
            self.assertEqual(captured, [421])
            self.assertEqual(callbacks, [1, 1])
            self.assertIsNone(manager.error)

    def test_default_template_uses_bundled_file_only(self):
        import script_core.audio_manager.manager as manager_module
        from script_core.audio_manager import default_template
        bundled = Path(manager_module.__file__).with_name('template.voice')
        with tempfile.TemporaryDirectory() as directory:
            # A template.voice in the working directory must be ignored.
            save_voice(Path(directory) / 'template.voice', tone())
            with patch('pathlib.Path.cwd', return_value=Path(directory)):
                self.assertEqual(default_template(), bundled)
        self.assertGreater(load_voice(bundled).size, 0)

    def test_start_reports_capture_failure_and_leaves_no_thread(self):
        window = SimpleNamespace(alive=True, process_id=9)
        def factory(pid):
            raise OSError('device unavailable')
        manager = AudioManager(window, factory)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target.voice'
            save_voice(path, tone())
            with patch('script_core.audio_manager.manager.check_supported'):
                with self.assertRaisesRegex(RuntimeError, '失败'):
                    manager.start(path)
        self.assertFalse(manager.listening)
        self.assertIsInstance(manager.error, OSError)
        self.assertIsNone(manager._worker)

    def test_start_rejects_dead_window_before_capture(self):
        manager = AudioManager(SimpleNamespace(alive=False, process_id=0), lambda pid: None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target.voice'
            save_voice(path, tone())
            with patch('script_core.audio_manager.manager.check_supported'):
                with self.assertRaisesRegex(RuntimeError, '窗口'):
                    manager.start(path)

    def test_action_exception_stops_listening_and_is_recorded(self):
        window = SimpleNamespace(alive=True, process_id=11)
        sample = tone()
        manager = AudioManager(window, lambda pid: _Capture(pid, [sample]))
        def explode():
            raise RuntimeError('action failed')
        manager.set_action(explode)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target.voice'
            save_voice(path, sample)
            with patch('script_core.audio_manager.manager.check_supported'):
                manager.start(path)
                deadline = time.monotonic() + 2
                while manager.error is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                manager.stop()
        self.assertIsInstance(manager.error, RuntimeError)
        self.assertFalse(manager.listening)

    def test_decode_rejects_truncated_voice_payload(self):
        from script_core.audio_manager.template import encode_voice
        with self.assertRaisesRegex(ValueError, '不完整|无效'):
            decode_voice(encode_voice(tone())[:-7])

    def test_facade_exposes_methods_and_rejects_invalid_threshold(self):
        window = SimpleNamespace(alive=True, process_id=3)
        api = _Audio(window, None)
        with self.assertRaises(ValueError):
            api.listen.threshold(1.1)
        api.listen.threshold(0.6)
        self.assertEqual(api._manager.threshold, 0.6)
        with self.assertRaises(TypeError):
            api.action('not callable')
        with self.assertRaisesRegex(FileNotFoundError, 'template.voice'):
            api.listen.start(Path('missing-template.voice'))


if __name__ == '__main__':
    unittest.main()
