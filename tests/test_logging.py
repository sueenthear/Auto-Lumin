import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

import numpy as np
from unittest.mock import Mock, patch

from script_core.facade import AutomationCore


class DoLoggingTests(unittest.TestCase):
    def make_do(self):
        core = SimpleNamespace(
            window=SimpleNamespace(hwnd=1),
            image=SimpleNamespace(screenshot=Mock(return_value=None)),
            input=Mock(),
        )
        with patch("script_core.facade.Interrupt"):
            return AutomationCore(core), core

    def test_logs_are_disabled_by_default(self):
        do, core = self.make_do()
        output = io.StringIO()

        with redirect_stdout(output):
            do.mouse.click.left
            do.keyboard.text("private text")

        self.assertEqual(output.getvalue(), "")
        core.input.click.assert_called_once_with("left")
        core.input.type_text.assert_called_once_with("private text", 0.0)

    def test_log_output_formats_source_time_and_message(self):
        do, _core = self.make_do()
        output = io.StringIO()

        with redirect_stdout(output):
            do.log.output(True)
            do.mouse.click.left

        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(
            lines[0],
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]"
            r"\[来源：do\.log\.output\]<True>$",
        )
        self.assertRegex(
            lines[1],
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]"
            r"\[来源：do\.mouse\.click\.left\]<left>$",
        )

    def test_disabling_logs_emits_toggle_then_silences_operations(self):
        do, _core = self.make_do()
        output = io.StringIO()

        with redirect_stdout(output):
            do.log.output(True)
            do.log.output(False)
            do.mouse.click.right

        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("[来源：do.log.output]<False>", lines[1])

    def test_mouse_setting_aliases_keep_their_source_paths(self):
        do, _core = self.make_do()
        output = io.StringIO()

        with redirect_stdout(output):
            do.log.output(True)
            do.mouse.set.abs("fps", 30)
            do.mouse.move.set.rel("duration", 0.2)

        self.assertIn("[来源：do.mouse.set.abs]<fps=30>", output.getvalue())
        self.assertIn("[来源：do.mouse.move.set.rel]<duration=0.2>", output.getvalue())

    def test_keyboard_text_log_does_not_include_text_contents(self):
        do, _core = self.make_do()
        output = io.StringIO()

        with redirect_stdout(output):
            do.log.output(True)
            do.keyboard.text("secret", interval=0.1)

        log = output.getvalue()
        self.assertIn("[来源：do.keyboard.text]<length=6, interval=0.1>", log)
        self.assertNotIn("secret", log)

    def test_pixel_facade_uses_screenshot_callback_and_logs_query(self):
        frame = np.array([[[0, 0, 255]]], dtype=np.uint8)
        core = SimpleNamespace(
            window=SimpleNamespace(hwnd=1),
            image=SimpleNamespace(screenshot=Mock(return_value=frame)),
            input=Mock(),
        )
        with patch("script_core.facade.Interrupt"):
            do = AutomationCore(core)
        output = io.StringIO()

        with redirect_stdout(output):
            do.log.output(True)
            self.assertEqual(do.pixel.point(0, 0), "FF0000")
            self.assertEqual(do.pixel.count("FF0000"), 1)

        self.assertIn("[来源：do.pixel.point]<point (0, 0)>", output.getvalue())
        self.assertIn("[来源：do.pixel.count]<count color='FF0000', tolerance=0>", output.getvalue())
        self.assertEqual(core.image.screenshot.call_count, 2)

    def test_log_output_requires_a_boolean(self):
        do, _core = self.make_do()
        for value in (1, 0, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                do.log.output(value)


if __name__ == "__main__":
    unittest.main()
