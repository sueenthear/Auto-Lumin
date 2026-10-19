import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from script_core.facade import _Execution, _Steps, _Window
from script_core.steps import load_steps, replay


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
        self.steps.wait(execution)

        self.assertFalse(execution._thread.is_alive())
        self.assertEqual(execution.state, "paused")
        self.input.key_down.assert_called_once_with("a")
        self.input.key_up.assert_called_once_with("a")
        self.assertEqual(execution._index, 2)

    def test_wait_blocks_until_worker_thread_finishes(self):
        started = threading.Event()
        release = threading.Event()
        finished_waiting = threading.Event()
        self.input.key_down.side_effect = lambda _key: (started.set(), release.wait())
        execution = _Execution(self.steps._core, self.recording, "pause")
        self.steps.start(execution)
        self.assertTrue(started.wait(timeout=1))

        waiter = threading.Thread(
            target=lambda: (self.steps.wait(execution), finished_waiting.set()),
            daemon=True,
        )
        waiter.start()
        self.assertFalse(finished_waiting.wait(timeout=0.05))

        release.set()
        waiter.join(timeout=1)
        self.assertTrue(finished_waiting.is_set())
        self.assertFalse(execution._worker_thread.is_alive())
        self.assertEqual(execution.state, "paused")

    def test_wait_returns_immediately_when_execution_has_not_started(self):
        execution = _Execution(self.steps._core, self.recording, "pause")
        self.assertIsNone(self.steps.wait(execution))

    def test_wait_keeps_waiting_while_worker_is_manually_paused(self):
        recording = {
            **self.recording,
            "actions": [{"type": "click", "button": "left", "timestamp_ns": 10_000_000_000}],
        }
        execution = _Execution(self.steps._core, recording, "pause")
        finished_waiting = threading.Event()
        self.steps.start(execution)
        deadline = time.monotonic() + 1
        while execution._state != "running" and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(execution._state, "running")
        self.steps.pause(execution)

        waiter = threading.Thread(
            target=lambda: (self.steps.wait(execution), finished_waiting.set()),
            daemon=True,
        )
        waiter.start()
        self.assertFalse(finished_waiting.wait(timeout=0.05))

        self.steps.abort(execution)
        waiter.join(timeout=1)
        self.assertTrue(finished_waiting.is_set())
        self.assertFalse(execution._worker_thread.is_alive())

    def test_wait_joins_worker_after_end_abort_disposes_execution(self):
        execution = _Execution(self.steps._core, {**self.recording, "actions": []}, "abort")
        self.steps.start(execution)

        self.assertIsNone(self.steps.wait(execution))
        self.assertTrue(execution._closed)
        self.assertFalse(execution._worker_thread.is_alive())

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

    def test_load_accepts_mouse_down_and_mouse_up_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mouse-hold.stepscript"
            self.recording["actions"] = [
                {"type": "mouse_down", "button": "left", "timestamp_ns": 0},
                {"type": "mouse_up", "button": "left", "timestamp_ns": 250_000_000},
            ]
            self.write_recording(path)

            loaded = load_steps(path)
            execution = self.steps.load(path)

            self.assertEqual(loaded["actions"][0]["type"], "mouse_down")
            self.assertEqual(loaded["actions"][1]["type"], "mouse_up")
            self.assertEqual(execution.state, "ready")
            self.steps.abort(execution)

    def test_start_dispatches_mouse_hold_transitions_in_timestamp_order(self):
        recording = {
            **self.recording,
            "actions": [
                {"type": "mouse_down", "button": "left", "timestamp_ns": 0},
                {"type": "mouse_up", "button": "left", "timestamp_ns": 250_000_000},
            ],
        }
        execution = _Execution(self.steps._core, recording, "pause")
        self.steps.start(execution)
        execution._thread.join(timeout=2)

        self.assertFalse(execution._thread.is_alive())
        self.assertEqual(self.input.method_calls, [
            unittest.mock.call.mouse_down("left"),
            unittest.mock.call.mouse_up("left"),
        ])
        self.assertEqual(execution.state, "paused")

    def test_standalone_replay_dispatches_mouse_hold_transitions(self):
        recording = {
            **self.recording,
            "actions": [
                {"type": "mouse_down", "button": "right", "timestamp_ns": 0},
                {"type": "mouse_up", "button": "right", "timestamp_ns": 250_000_000},
            ],
        }
        core = SimpleNamespace(input=self.input, window=SimpleNamespace(activate=Mock(return_value=True)))

        with patch("script_core.steps.time.sleep"):
            replay(recording, core, activate=False)

        self.assertEqual(self.input.method_calls, [
            unittest.mock.call.mouse_down("right"),
            unittest.mock.call.mouse_up("right"),
        ])

    def test_standalone_replay_releases_mouse_button_on_error(self):
        self.input.click.side_effect = RuntimeError("input failed")
        recording = {
            **self.recording,
            "actions": [
                {"type": "mouse_down", "button": "right", "timestamp_ns": 0},
                {"type": "click", "button": "left", "timestamp_ns": 1},
            ],
        }
        core = SimpleNamespace(input=self.input, window=SimpleNamespace(activate=Mock(return_value=True)))

        with patch("script_core.steps.time.sleep"), self.assertRaisesRegex(RuntimeError, "input failed"):
            replay(recording, core, activate=False)

        self.input.mouse_down.assert_called_once_with("right")
        self.input.mouse_up.assert_called_once_with("right")

    def test_mouse_button_is_released_when_later_action_fails(self):
        self.input.click.side_effect = RuntimeError("input failed")
        recording = {
            **self.recording,
            "actions": [
                {"type": "mouse_down", "button": "right", "timestamp_ns": 0},
                {"type": "click", "button": "left", "timestamp_ns": 1},
            ],
        }
        execution = _Execution(self.steps._core, recording, "pause")
        execution._resume.set()
        _Steps._run(execution)

        self.assertEqual(execution.state, "failed")
        self.input.mouse_down.assert_called_once_with("right")
        self.input.mouse_up.assert_called_once_with("right")

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
