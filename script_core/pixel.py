from __future__ import annotations

import re

import numpy as np


_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")


class Pixel:
    """RGB pixel queries over the target window's client-area screenshot."""

    def __init__(self, screenshot):
        self._screenshot = screenshot

    def point(self, x: int, y: int) -> str:
        image = self._screenshot()
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] < 3:
            raise ValueError("Screenshot must be a BGR or BGRA image")
        height, width = image.shape[:2]
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError(f"Pixel coordinate {(x, y)} is outside image bounds {(width, height)}")
        blue, green, red = (int(channel) for channel in image[y, x, :3])
        return f"{red:02X}{green:02X}{blue:02X}"

    def count(self, target: str, tolerance: float = 0) -> int:
        if not isinstance(target, str) or not _COLOR.fullmatch(target):
            raise ValueError("target must be a six-digit RGB hex string without '#'")
        if not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= 1:
            raise ValueError("tolerance must be between 0 and 1")
        image = self._screenshot()
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] < 3:
            raise ValueError("Screenshot must be a BGR or BGRA image")
        rgb = image[:, :, :3][:, :, ::-1].astype(np.int16)
        expected = np.array([int(target[index:index + 2], 16) for index in (0, 2, 4)], dtype=np.int16)
        max_difference = round(tolerance * 255)
        matches = np.all(np.abs(rgb - expected) <= max_difference, axis=2)
        return int(np.count_nonzero(matches))
