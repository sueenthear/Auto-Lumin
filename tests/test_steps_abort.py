import time
import unittest
from types import SimpleNamespace

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
