import queue
import unittest
from unittest.mock import patch

from dev_tool import audio_recorder


class AudioRecorderHotkeyTests(unittest.TestCase):
    def test_global_f8_and_f9_enqueue_start_and_stop(self):
        recorder = audio_recorder.VoiceRecorder.__new__(audio_recorder.VoiceRecorder)
        recorder._hotkey_commands = queue.Queue()

        with patch.object(audio_recorder, "GlobalHotKeys") as global_hotkeys:
            recorder._start_hotkeys()
            callbacks = global_hotkeys.call_args.args[0]
            callbacks["<f8>"]()
            callbacks["<f9>"]()

        self.assertEqual(recorder._hotkey_commands.get_nowait(), "start")
        self.assertEqual(recorder._hotkey_commands.get_nowait(), "stop")
        global_hotkeys.return_value.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
