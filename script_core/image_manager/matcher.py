from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class TemplateMatch:
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


class TemplateMatcher:
    """Load Unicode-path images and locate templates, respecting alpha masks."""

    @staticmethod
    def load_image(path: str | Path, *, unchanged: bool = True) -> np.ndarray:
        image_path = Path(path)
        try:
            encoded = np.fromfile(image_path, dtype=np.uint8)
        except OSError as exc:
            raise FileNotFoundError(f"Could not read image: {image_path}") from exc
        if encoded.size == 0:
            raise ValueError(f"Image file is empty: {image_path}")
        mode = cv2.IMREAD_UNCHANGED if unchanged else cv2.IMREAD_COLOR
        image = cv2.imdecode(encoded, mode)
        if image is None:
            raise ValueError(f"Unsupported or corrupt image: {image_path}")
        return image

    @staticmethod
    def save_image(path: str | Path, image: np.ndarray) -> None:
        image_path = Path(path)
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise ValueError("Image must be a non-empty NumPy array")
        extension = image_path.suffix
        if not extension:
            raise ValueError(f"Image path must have a format extension: {image_path}")
        success, encoded = cv2.imencode(extension, image)
        if not success:
            raise ValueError(f"Unsupported image format or encoding failed: {extension}")
        try:
            image_path.write_bytes(encoded.tobytes())
        except OSError as exc:
            raise OSError(f"Could not write image: {image_path}") from exc

    @classmethod
    def find(
        cls,
        source: np.ndarray,
        template: np.ndarray,
        *,
        threshold: float = 0.8,
    ) -> TemplateMatch | None:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        source_bgr = cls._to_bgr(source)
        template_bgr = cls._to_bgr(template)
        height, width = template_bgr.shape[:2]
        source_height, source_width = source_bgr.shape[:2]
        if height > source_height or width > source_width:
            return None

        if template.ndim == 3 and template.shape[2] == 4:
            alpha = template[:, :, 3]
            if not np.any(alpha):
                raise ValueError("Template alpha channel is fully transparent")
            mask = np.where(alpha > 0, 255, 0).astype(np.uint8)
            source_gray = cv2.cvtColor(source_bgr, cv2.COLOR_BGR2GRAY)
            template_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
            differences = cv2.matchTemplate(
                source_gray,
                template_gray,
                cv2.TM_SQDIFF,
                mask=mask,
            )
            minimum_difference, _, location, _ = cv2.minMaxLoc(differences)
            visible_pixels = max(1, int(np.count_nonzero(mask)))
            max_difference = 255.0 * 255.0 * visible_pixels
            score = 1.0 - minimum_difference / max_difference
        else:
            scores = cv2.matchTemplate(source_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(scores)

        if not np.isfinite(score) or score < threshold:
            return None
        return TemplateMatch(location[0], location[1], width, height, float(score))

    @staticmethod
    def _to_bgr(image: np.ndarray) -> np.ndarray:
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise ValueError("Image must be a non-empty NumPy array")
        if image.ndim == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if image.ndim != 3:
            raise ValueError("Image must have 2 or 3 dimensions")
        channels = image.shape[2]
        if channels == 3:
            return image
        if channels == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        raise ValueError(f"Unsupported image channel count: {channels}")
