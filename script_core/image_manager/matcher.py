from __future__ import annotations

from dataclasses import dataclass
import gc
from pathlib import Path
import traceback
import warnings

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

    # Output-coordinate tiles, with template-sized overlap in the input. Every
    # candidate is visited, including matches spanning a tile boundary.
    _TILE_EDGE = 256

    @staticmethod
    def _is_memory_error(error: BaseException) -> bool:
        if isinstance(error, MemoryError):
            return True
        if not isinstance(error, cv2.error):
            return False
        return (getattr(error, "code", None) == -4
                or any(text in str(error).lower() for text in
                       ("insufficient memory", "outofmemoryerror", "failed to allocate")))

    @staticmethod
    def _release_error_frames(error: BaseException) -> None:
        # Failed OpenCV calls can retain converted images through their traceback.
        # Drop those references before collecting/retrying, not after the retry.
        traceback.clear_frames(error.__traceback__)
        error.__traceback__ = None

    @classmethod
    def find(
        cls,
        source: np.ndarray,
        template: np.ndarray,
        *,
        threshold: float = 0.8,
        method: int = cv2.TM_CCOEFF_NORMED,
    ) -> TemplateMatch | None:
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be between 0 and 1")
        if method not in (cv2.TM_CCOEFF_NORMED, cv2.TM_SQDIFF_NORMED, cv2.TM_CCORR_NORMED):
            raise ValueError("unsupported template match method")
        try:
            return cls._find_once(source, template, threshold, method, tiled=False)
        except (cv2.error, MemoryError) as exc:
            if not cls._is_memory_error(exc):
                raise
            cls._release_error_frames(exc)
        # This is exceptional recovery only; never force GC on each poll. Do not
        # change global OpenCV thread/OpenCL settings shared by other callers.
        gc.collect()
        warnings.warn("Template matching ran out of memory; retrying once with tiled matching",
                      RuntimeWarning, stacklevel=2)
        try:
            return cls._find_once(source, template, threshold, method, tiled=True)
        except (cv2.error, MemoryError) as exc:
            if not cls._is_memory_error(exc):
                raise
            cls._release_error_frames(exc)
        raise MemoryError(
            "Insufficient memory for template matching even after tiled recovery "
            f"(source={getattr(source, 'shape', None)}, template={getattr(template, 'shape', None)}). "
            "Close memory-heavy applications, reduce the capture/ROI size, or increase the Windows page file."
        ) from None

    @classmethod
    def _find_once(cls, source, template, threshold, method, *, tiled):
        # Validate without converting the whole source: low-memory recovery must
        # crop first, then convert, so full-frame BGR/gray copies are not retained.
        for image in (source, template):
            if not isinstance(image, np.ndarray) or image.size == 0:
                raise ValueError("Image must be a non-empty NumPy array")
            if image.ndim not in (2, 3):
                raise ValueError("Image must have 2 or 3 dimensions")
            if image.ndim == 3 and image.shape[2] not in (3, 4):
                raise ValueError(f"Unsupported image channel count: {image.shape[2]}")
        height, width = template.shape[:2]
        source_height, source_width = source.shape[:2]
        if height > source_height or width > source_width:
            return None
        template_bgr = cls._to_bgr(template)
        mask = None
        max_difference = None
        # Preserve the existing binary-alpha, grayscale SQDIFF scoring for the
        # default method. Explicit alternative algorithms keep their BGR scoring.
        if template.ndim == 3 and template.shape[2] == 4:
            alpha = template[:, :, 3]
            if not np.any(alpha):
                raise ValueError("Template alpha channel is fully transparent")
            if method == cv2.TM_CCOEFF_NORMED:
                mask = np.where(alpha > 0, 255, 0).astype(np.uint8)
                max_difference = 255.0 * 255.0 * max(1, int(np.count_nonzero(mask)))
                template_bgr = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
        output_height, output_width = source_height - height + 1, source_width - width + 1
        step_y = min(cls._TILE_EDGE, output_height) if tiled else output_height
        step_x = min(cls._TILE_EDGE, output_width) if tiled else output_width
        best_score, best_location = float("-inf"), None
        for y in range(0, output_height, step_y):
            for x in range(0, output_width, step_x):
                bottom = min(y + step_y, output_height) + height - 1
                right = min(x + step_x, output_width) + width - 1
                haystack = cls._to_bgr(source[y:bottom, x:right])
                if mask is not None:
                    haystack = cv2.cvtColor(haystack, cv2.COLOR_BGR2GRAY)
                    values = cv2.matchTemplate(haystack, template_bgr, cv2.TM_SQDIFF, mask=mask)
                else:
                    values = cv2.matchTemplate(haystack, template_bgr, method)
                low, high, low_pos, high_pos = cv2.minMaxLoc(values)
                del values, haystack
                if mask is not None:
                    score, position = 1.0 - low / max_difference, low_pos
                elif method == cv2.TM_SQDIFF_NORMED:
                    score, position = 1.0 - low, low_pos
                else:
                    score, position = high, high_pos
                location = (position[0] + x, position[1] + y)
                if np.isfinite(score) and (score > best_score or (
                    score == best_score and best_location is not None
                    and location[::-1] < best_location[::-1]
                )):
                    best_score, best_location = score, location
        if best_location is None or best_score < threshold:
            return None
        return TemplateMatch(best_location[0], best_location[1], width, height, float(best_score))

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
