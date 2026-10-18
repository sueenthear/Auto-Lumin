"""Reusable Windows automation primitives."""

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
