import unittest

import numpy as np

from script_core.pixel import Pixel


class PixelTests(unittest.TestCase):
    def setUp(self):
        # Pixel receives screenshots in OpenCV BGRA channel order.
        self.image = np.array(
            [
                [[0, 0, 255, 255], [0, 8, 255, 255]],
                [[255, 0, 0, 255], [0, 0, 0, 255]],
            ],
            dtype=np.uint8,
        )
        self.pixel = Pixel(lambda: self.image)

    def test_point_returns_rgb_hex(self):
        self.assertEqual(self.pixel.point(0, 0), "FF0000")
        self.assertEqual(self.pixel.point(0, 1), "0000FF")

    def test_count_exact_and_with_tolerance(self):
        self.assertEqual(self.pixel.count("FF0000"), 1)
        self.assertEqual(self.pixel.count("FF0000", tolerance=8 / 255), 2)
        self.assertEqual(self.pixel.count("123456", tolerance=1), self.image.shape[0] * self.image.shape[1])

    def test_rejects_invalid_color_tolerance_and_coordinates(self):
        with self.assertRaises(ValueError):
            self.pixel.point(2, 0)
        with self.assertRaises(ValueError):
            self.pixel.count("#FF0000")
        with self.assertRaises(ValueError):
            self.pixel.count("FF0000", tolerance=1.01)


if __name__ == "__main__":
    unittest.main()
