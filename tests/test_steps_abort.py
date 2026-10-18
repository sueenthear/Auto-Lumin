import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from script_core.facade import _Execution, _Steps


class StepsAbortTests(unittest.TestCase):
    def setUp(self):
        self.core = SimpleNamespace(input=SimpleNamespace())

    def assert_disposed(self, execution):
        self.assertIsNone(execution.core)
        self.assertIsNone(execution.steps)
        self.assertIsNone(execution.end)
        for operation in (
            lambda: execution.state,
            lambda: execution.time,
            lambda: _Steps.start(execution),
            lambda: _Steps.pause(execution),
            lambda: _Steps.reset(execution),
            lambda: _Steps.abort(execution),
        ):
            with self.assertRaises(RuntimeError):
                operation()

    def test_abort_disposes_an_unstarted_execution(self):
        execution = _Execution(self.core, {"actions": []}, "pause")
        _Steps.abort(execution)
        self.assert_disposed(execution)

    def test_abort_waits_for_a_running_thread_and_disposes(self):
        execution = _Execution(
            self.core,
            {"actions": [{"timestamp_ns": 10_000_000_000, "type": "click", "button": "left"}]},
            "pause",
        )
        _Steps.start(execution)
        deadline = time.monotonic() + 1
        while execution._state != "running" and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(execution._state, "running")

        _Steps.abort(execution)

        self.assertIsNone(execution._thread)
        self.assert_disposed(execution)

    def test_abort_releases_mouse_button_held_by_stepscript(self):
        input_manager = Mock()
        down_started = threading.Event()

        def mouse_down(button):
            down_started.set()

        input_manager.mouse_down.side_effect = mouse_down
        execution = _Execution(
            SimpleNamespace(input=input_manager),
            {"actions": [
                {"timestamp_ns": 0, "type": "mouse_down", "button": "left"},
                {"timestamp_ns": 10_000_000_000, "type": "mouse_up", "button": "left"},
            ]},
            "pause",
        )
        _Steps.start(execution)
        self.assertTrue(down_started.wait(timeout=1))

        _Steps.abort(execution)

        input_manager.mouse_down.assert_called_once_with("left")
        input_manager.mouse_up.assert_called_once_with("left")
        self.assert_disposed(execution)

    def test_end_abort_disposes_when_execution_finishes(self):
        execution = _Execution(self.core, {"actions": []}, "abort")
        _Steps.start(execution)
        deadline = time.monotonic() + 1
        while not execution._closed and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertTrue(execution._closed)
        self.assert_disposed(execution)


if __name__ == "__main__":
    unittest.main()
