import unittest
from unittest.mock import patch

import win32con

from script_core.window_manager.core import WindowManager


class WindowSizingTests(unittest.TestCase):
    def test_set_client_size_adjusts_outer_dimensions_via_user32(self):
        window = WindowManager.__new__(WindowManager)
        window.hwnd = 123
        style = win32con.WS_OVERLAPPEDWINDOW
        with (
            patch("script_core.window_manager.core.win32gui.IsWindow", return_value=True),
            patch("script_core.window_manager.core.win32gui.GetWindowLong", side_effect=[style, 0]),
            patch("script_core.window_manager.core.win32gui.GetMenu", return_value=0),
            patch("script_core.window_manager.core.win32gui.SetWindowPos") as set_pos,
        ):
            window.set_client_size(1600, 900)

        set_pos.assert_called_once()
        hwnd, _, _, _, width, height, flags = set_pos.call_args.args
        self.assertEqual(hwnd, 123)
        self.assertGreater(width, 1600)
        self.assertGreater(height, 900)
        self.assertEqual(flags, win32con.SWP_NOMOVE | win32con.SWP_NOZORDER)

    def test_set_client_size_rejects_invalid_dimensions_before_touching_window(self):
        window = WindowManager.__new__(WindowManager)
        with patch("script_core.window_manager.core.win32gui.IsWindow") as is_window:
            with self.assertRaises(ValueError):
                window.set_client_size(0, 900)
            is_window.assert_not_called()


if __name__ == "__main__":
    unittest.main()
