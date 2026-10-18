from __future__ import annotations

from typing import Any

from ..window_manager import WindowManager
from .keyboard import KeyboardInput
from .mouse import MouseInput


class InputManager:
    """Foreground keyboard and mouse input directed at one managed window."""

    def __init__(
        self,
        window: WindowManager,
        *,
        keyboard: KeyboardInput | None = None,
        mouse: MouseInput | None = None,
    ) -> None:
        self.window = window
        self.keyboard = keyboard or KeyboardInput()
        self.mouse = mouse or MouseInput()

    def key_down(self, key: str | int | Any) -> None:
        self.window.require_foreground()
        self.keyboard.key_down(key)

    def key_up(self, key: str | int | Any) -> None:
        self.window.require_foreground()
        self.keyboard.key_up(key)

    def press_key(self, key: str | int | Any, down_time: float = 0.02) -> None:
        self.window.require_foreground()
        self.keyboard.press(key, down_time)

    def press_character(self, character: str, down_time: float = 0.02) -> None:
        self.window.require_foreground()
        self.keyboard.press_character(character, down_time)

    def type_text(self, text: str, interval: float = 0.0) -> None:
        self.window.require_foreground()
        self.keyboard.type_text(text, interval)

    def move_to(
        self,
        x: int,
        y: int,
        *,
        duration: float = 0.2,
        fps: int = 60,
    ) -> None:
        """Move smoothly to a point relative to the target window's client area."""
        self.window.require_foreground()
        screen_x, screen_y = self.window.client_to_screen(x, y)
        current_x, current_y = self.mouse.position
        self.mouse.move_by(
            screen_x - current_x,
            screen_y - current_y,
            duration=duration,
            fps=fps,
        )

    def move_to_screen(
        self,
        x: int,
        y: int,
        *,
        duration: float = 0.2,
        fps: int = 60,
    ) -> None:
        """Move smoothly to an absolute screen coordinate."""
        self.window.require_foreground()
        current_x, current_y = self.mouse.position
        self.mouse.move_by(x - current_x, y - current_y, duration=duration, fps=fps)

    def move_by(self, dx: int, dy: int, *, duration: float = 0.2, fps: int = 60) -> None:
        self.window.require_foreground()
        self.mouse.move_by(dx, dy, duration=duration, fps=fps)

    def click_at(
        self,
        x: int,
        y: int,
        button: str | Any = "LEFT",
        clicks: int = 1,
        interval: float = 0.05,
    ) -> None:
        """Click at a point relative to the target window's client area."""
        self.move_to(x, y)
        self.mouse.click(button, clicks, interval)

    def mouse_down(self, button: str | Any = "LEFT") -> None:
        self.window.require_foreground()
        self.mouse.button_down(button)

    def mouse_up(self, button: str | Any = "LEFT") -> None:
        self.window.require_foreground()
        self.mouse.button_up(button)

    def click(self, button: str | Any = "LEFT", clicks: int = 1, interval: float = 0.05) -> None:
        self.window.require_foreground()
        self.mouse.click(button, clicks, interval)

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        self.window.require_foreground()
        self.mouse.scroll(dx, dy)
