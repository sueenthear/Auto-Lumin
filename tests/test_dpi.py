"""DPI 缩放回归：Win32 客户区坐标必须与 WGC 物理帧对齐。"""
import ctypes
import os
import unittest

import numpy as np

from script_core._dpi import ensure_process_dpi_awareness
from script_core.image_manager.capture import WindowCapture


@unittest.skipUnless(os.name == "nt", "Windows only")
class ProcessDpiTests(unittest.TestCase):
    def test_package_import_makes_process_dpi_aware(self):
        state = ensure_process_dpi_awareness()
        self.assertIn(state, {"per-monitor-v2", "per-monitor", "system"})

        awareness = ctypes.c_int()
        result = ctypes.windll.shcore.GetProcessDpiAwareness(
            None, ctypes.byref(awareness))
        self.assertEqual(result, 0)
        self.assertNotEqual(awareness.value, 0, "进程仍处于 DPI 虚拟化状态")


class ClientCropTests(unittest.TestCase):
    class FakeWindow:
        hwnd = 0x10

        def __init__(self, *, crop, physical_size, expected_size):
            self._crop = crop
            self._physical_size = physical_size
            self._expected_size = expected_size

        @property
        def client_size(self):
            return self._expected_size

        @property
        def client_size_physical(self):
            return self._physical_size

        def client_rect_in_window_capture(self):
            return self._crop

    @staticmethod
    def make_capture(window):
        capture = WindowCapture.__new__(WindowCapture)
        capture.window = window
        return capture

    def test_matching_crop_is_returned_as_an_independent_copy(self):
        window = self.FakeWindow(
            crop=(1, 1, 5, 4), physical_size=(4, 3), expected_size=(4, 3))
        frame = np.zeros((5, 6, 4), dtype=np.uint8)
        patch = self.make_capture(window)._crop_client_area(frame, (4, 3))
        self.assertEqual(patch.shape, (3, 4, 4))
        patch[:] = 255
        self.assertEqual(int(frame[1, 1, 0]), 0)

    def test_physical_crop_is_reduced_to_logical_size(self):
        # Thread-level DPI override: the crop is physical (125%), while the
        # public client size remains DPI-virtualized at 1600x900.
        window = self.FakeWindow(
            crop=(1, 31, 2001, 1156),
            physical_size=(2000, 1125),
            expected_size=(1600, 900),
        )
        frame = np.zeros((1166, 2004, 4), dtype=np.uint8)
        patch = self.make_capture(window)._crop_client_area(frame, (1600, 900))
        self.assertEqual(patch.shape, (900, 1600, 4))

    def test_one_pixel_border_difference_is_normalized(self):
        window = self.FakeWindow(
            crop=(1, 31, 802, 932),
            physical_size=(800, 900),
            expected_size=(800, 900),
        )
        frame = np.zeros((933, 803, 4), dtype=np.uint8)
        patch = self.make_capture(window)._crop_client_area(frame, (800, 900))
        self.assertEqual(patch.shape, (900, 800, 4))

    def test_crop_outside_frame_is_rejected_with_dpi_diagnostic(self):
        window = self.FakeWindow(
            crop=(-3, -16, 1598, 885),
            physical_size=(1601, 901),
            expected_size=(1600, 900),
        )
        frame = np.zeros((1166, 2004, 4), dtype=np.uint8)
        with self.assertRaisesRegex(RuntimeError, "exceeds WGC frame"):
            self.make_capture(window)._crop_client_area(frame, (1600, 900))


if __name__ == "__main__":
    unittest.main()
