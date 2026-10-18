import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from script_core.facade import _Execution, _Steps, _Window


class StepsExecutionTests(unittest.TestCase):
    def setUp(self):
        self.input = Mock()
        self.steps = _Steps(SimpleNamespace(input=self.input))
        self.recording = {
            "format": "ok-lumin.stepscript",
            "version": 2,
            "timestamp_unit": "relative_ns",
            "info": {"duration_ns": 12, "action_count": 2},
            "actions": [
                {"type": "key_down", "key": "a", "timestamp_ns": 0},
                {"type": "key_up", "key": "a", "timestamp_ns": 12},
            ],
        }

    def write_recording(self, path):
        path.write_text(json.dumps(self.recording), encoding="utf-8")

    def test_load_and_loads_return_executables_and_info(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "route.stepscript"
            self.write_recording(path)

            execution = self.steps.load(path)
            loaded = self.steps.loads(directory)

            self.assertEqual(execution.state, "ready")
            self.assertEqual(set(loaded), {"route"})
            self.assertEqual(loaded["route"].state, "ready")
            self.assertEqual(
                self.steps.info(execution),
                {
                    "format": "ok-lumin.stepscript",
                    "version": 2,
                    "timestamp_unit": "relative_ns",
                    "info": {"duration_ns": 12, "action_count": 2},
                },
            )
            self.assertFalse(hasattr(self.steps, "execute"))

            self.steps.abort(execution)
            self.steps.abort(loaded["route"])

    def test_start_dispatches_actions_and_finishes_paused(self):
        execution = _Execution(self.steps._core, self.recording, "pause")
        self.steps.start(execution)
        execution._thread.join(timeout=2)

        self.assertFalse(execution._thread.is_alive())
        self.assertEqual(execution.state, "paused")
        self.input.key_down.assert_called_once_with("a")
        self.input.key_up.assert_called_once_with("a")
        self.assertEqual(execution._index, 2)

    def test_deadline_crossing_during_wait_does_not_sleep_negative_duration(self):
        recording = {
            **self.recording,
            "actions": [{"type": "click", "button": "left", "timestamp_ns": 100}],
        }
        execution = _Execution(self.steps._core, recording, "pause")
        execution._resume.set()

        with (
            patch("script_core.facade.time.perf_counter_ns", side_effect=[0, 99, 101, 101]),
            patch("script_core.facade.time.sleep") as sleep,
        ):
            _Steps._run(execution)

        self.assertTrue(all(call.args[0] >= 0 for call in sleep.call_args_list))
        self.input.click.assert_called_once_with("left")
        self.assertEqual(execution.state, "paused")

    def test_start_records_input_error_in_execution(self):
        self.input.key_down.side_effect = RuntimeError("input failed")
        execution = _Execution(self.steps._core, self.recording, "pause")
        self.steps.start(execution)
        execution._thread.join(timeout=2)

        self.assertEqual(execution.state, "failed")
        self.assertIsInstance(execution.error, RuntimeError)
        self.assertEqual(str(execution.error), "input failed")

    def test_info_falls_back_to_last_action_timestamp(self):
        recording = {
            **self.recording,
            "info": {},
            "actions": [{"type": "click", "button": "left", "timestamp_ns": 42}],
        }
        execution = _Execution(self.steps._core, recording, "pause")
        self.assertEqual(
            self.steps.info(execution)["info"],
            {"duration_ns": 42, "action_count": 1},
        )


class WindowSizeFacadeTests(unittest.TestCase):
    def test_two_argument_size_preserves_position(self):
        window_manager = Mock()
        window_manager.hwnd = 1
        window_manager.client_size = (800, 600)
        window_manager.position = (40, 50)
        window = _Window(SimpleNamespace(window=window_manager))

        window.size(1600, 900)

        window_manager.set_position.assert_called_once_with(40, 50)
        window_manager.set_client_size.assert_called_once_with(1600, 900)


if __name__ == "__main__":
    unittest.main()
