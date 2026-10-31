"""Reusable Windows automation primitives."""

from ._dpi import ensure_process_dpi_awareness

# Must run before any window is created: it aligns Win32/client coordinates
# with the physical pixels returned by Windows Graphics Capture.
ensure_process_dpi_awareness()

from .image_manager import ImageManager, TemplateMatch, TemplateMatcher, WindowCapture
from .input_manager import InputManager, KeyboardInput, MouseInput
from .window_manager import WindowManager
from .core import Core, ScriptCore
from .pixel import Pixel
from .steps import load_steps, replay

__all__ = [
    "ImageManager",
    "ScriptCore",
    "Core",
    "Pixel",
    "InputManager",
    "KeyboardInput",
    "MouseInput",
    "TemplateMatch",
    "TemplateMatcher",
    "WindowCapture",
    "WindowManager",
    "load_steps",
    "replay",
]
