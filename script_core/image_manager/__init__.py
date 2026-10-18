from __future__ import annotations

from pathlib import Path

import numpy as np

from .capture import WindowCapture
from .matcher import TemplateMatch, TemplateMatcher
from ..window_manager import WindowManager


class ImageManager:
    """Window screenshot and template-matching facade."""

    def __init__(self, window: WindowManager) -> None:
        self.window = window
        self.capture = WindowCapture(window)
        self.matcher = TemplateMatcher()

    def screenshot(self, timeout: float = 5.0) -> np.ndarray:
        return self.capture.screenshot(timeout)

    @staticmethod
    def load_image(path: str | Path, *, unchanged: bool = True) -> np.ndarray:
        return TemplateMatcher.load_image(path, unchanged=unchanged)

    @staticmethod
    def save_image(path: str | Path, image: np.ndarray) -> None:
        TemplateMatcher.save_image(path, image)

    def find(
        self,
        template: str | Path | np.ndarray,
        *,
        threshold: float = 0.8,
        source: np.ndarray | None = None,
    ) -> TemplateMatch | None:
        image = (
            TemplateMatcher.load_image(template)
            if isinstance(template, (str, Path))
            else template
        )
        frame = source if source is not None else self.screenshot()
        return TemplateMatcher.find(frame, image, threshold=threshold)


__all__ = ["ImageManager", "TemplateMatch", "TemplateMatcher", "WindowCapture"]
