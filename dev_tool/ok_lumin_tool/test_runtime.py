import sys
import types
import unittest
from unittest.mock import patch

from .model import normalize_actions
from .runtime import InputRecorder, PlaybackWorker


class Button:
    name = "left"


class MouseButton:
    left = "left-button"


class Controller:
    def __init__(self):
        self.events = []

    def press(self, button):
        self.events.append(("press", button))

    def release(self, button):
        self.events.append(("release", button))


class MouseModule(types.ModuleType):
    def __init__(self):
        super().__init__("pynput.mouse")
        self.Button = MouseButton
        self.controller = Controller()

    def Controller(self):
        return self.controller


class KeyboardModule(types.ModuleType):
    def __init__(self):
        super().__init__("pynput.keyboard")
        self.Key = types.SimpleNamespace()
        self.controller = Controller()

    def Controller(self):
        return self.controller


class MouseHoldTests(unittest.TestCase):
    def test_recorder_keeps_mouse_down_and_up_timestamps(self):
        recorder = InputRecorder()
        recorder.recording = True
        with patch("dev_tool.ok_lumin_tool.runtime.time.perf_counter_ns", side_effect=[1_000, 1_000_000_001]):
            recorder._on_click(0, 0, Button(), True)
            recorder._on_click(0, 0, Button(), False)

        actions = recorder.drain_pending()
        self.assertEqual([action["type"] for action in actions], ["mouse_down", "mouse_up"])
        self.assertEqual([action["timestamp_ns"] for action in actions], [0, 1_000_000_001 - 1_000])
        self.assertEqual([action["button"] for action in actions], ["left", "left"])

    def test_stop_repairs_mouse_button_left_held(self):
        recorder = InputRecorder()
        recorder.recording = True
        with patch("dev_tool.ok_lumin_tool.runtime.time.perf_counter_ns", side_effect=[10, 30]):
            recorder._on_click(0, 0, Button(), True)
            actions = recorder.stop()

        self.assertEqual([action["type"] for action in actions], ["mouse_down", "mouse_up"])
        self.assertTrue(actions[-1]["repaired"])
        self.assertEqual(actions[-1]["timestamp_ns"], 20)
        self.assertEqual(recorder._held_buttons, set())

    def test_normalize_repairs_open_mouse_hold(self):
        actions = normalize_actions([
            {"type": "mouse_down", "button": "left", "timestamp_ns": 50},
        ])
        self.assertEqual([action["type"] for action in actions], ["mouse_down", "mouse_up"])
        self.assertTrue(actions[-1]["repaired"])
        self.assertGreater(actions[-1]["timestamp_ns"], actions[0]["timestamp_ns"])

    def test_legacy_click_remains_an_instant_press_and_release(self):
        pynput = types.ModuleType("pynput")
        keyboard = KeyboardModule()
        mouse = MouseModule()
        pynput.keyboard = keyboard
        pynput.mouse = mouse
        worker = PlaybackWorker([
            {"type": "click", "button": "left", "timestamp_ns": 0},
        ], lambda _action: None, lambda: None)

        with patch.dict(sys.modules, {"pynput": pynput, "pynput.keyboard": keyboard, "pynput.mouse": mouse}), \
             patch.object(PlaybackWorker, "_wait_until", return_value=True):
            worker.run()

        self.assertEqual(mouse.controller.events, [("press", "left-button"), ("release", "left-button")])
        self.assertEqual(worker.held_buttons, set())

    def test_playback_holds_button_until_mouse_up_timestamp(self):
        pynput = types.ModuleType("pynput")
        keyboard = KeyboardModule()
        mouse = MouseModule()
        pynput.keyboard = keyboard
        pynput.mouse = mouse
        actions = [
            {"type": "mouse_down", "button": "left", "timestamp_ns": 0},
            {"type": "mouse_up", "button": "left", "timestamp_ns": 5_000_000_000},
        ]
        worker = PlaybackWorker(actions, lambda _action: None, lambda: None)
        deadlines = []

        def wait_until(deadline_ns, _stopped, _resume):
            deadlines.append(deadline_ns)
            return True

        with patch.dict(sys.modules, {"pynput": pynput, "pynput.keyboard": keyboard, "pynput.mouse": mouse}), \
             patch.object(PlaybackWorker, "_wait_until", side_effect=wait_until):
            worker.run()

        self.assertEqual(mouse.controller.events, [("press", "left-button"), ("release", "left-button")])
        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines[1] - deadlines[0], 5_000_000_000)
        self.assertEqual(worker.held_buttons, set())

    def test_playback_releases_button_on_early_stop(self):
        pynput = types.ModuleType("pynput")
        keyboard = KeyboardModule()
        mouse = MouseModule()
        pynput.keyboard = keyboard
        pynput.mouse = mouse
        worker = PlaybackWorker([
            {"type": "mouse_down", "button": "left", "timestamp_ns": 0},
            {"type": "mouse_up", "button": "left", "timestamp_ns": 10_000_000_000},
        ], lambda _action: None, lambda: None)
        waits = 0

        def stop_on_second_event(_deadline_ns, _stopped, _resume):
            nonlocal waits
            waits += 1
            if waits == 1:
                return True
            worker.stop()
            return False

        with patch.dict(sys.modules, {"pynput": pynput, "pynput.keyboard": keyboard, "pynput.mouse": mouse}), \
             patch.object(PlaybackWorker, "_wait_until", side_effect=stop_on_second_event):
            worker.run()

        self.assertEqual(mouse.controller.events, [("press", "left-button"), ("release", "left-button")])
        self.assertEqual(worker.held_buttons, set())


if __name__ == "__main__":
    unittest.main()
