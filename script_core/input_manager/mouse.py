from __future__ import annotations

import time
from typing import Any

from pynput.mouse import Button, Controller


class MouseInput:
    BUTTONS = {
        "LEFT": Button.left,
        "RIGHT": Button.right,
        "MIDDLE": Button.middle,
        "X1": Button.x1,
        "X2": Button.x2,
        "XFRONT": Button.x1,
        "XBEHIND": Button.x2,
    }

    def __init__(self, controller: Controller | None = None) -> None:
        self.controller = controller or Controller()

    @classmethod
    def resolve_button(cls, button: str | Any) -> Any:
        if not isinstance(button, str):
            return button
        try:
            return cls.BUTTONS[button.upper()]
        except KeyError as exc:
            raise ValueError(f"Unknown mouse button: {button!r}") from exc

    @property
    def position(self) -> tuple[int, int]:
        return self.controller.position

    def move_to(self, x: int, y: int) -> None:
        self.controller.position = (x, y)

    def move_by(
        self,
        dx: int,
        dy: int,
        *,
        duration: float = 0.2,
        fps: int = 60,
    ) -> None:
        """Move by a relative screen offset over duration at the requested frame rate."""
        if duration < 0:
            raise ValueError("duration cannot be negative")
        if fps < 1:
            raise ValueError("fps must be positive")
        steps = max(1, round(duration * fps))
        start_x, start_y = self.position
        for step in range(1, steps + 1):
            x = start_x + round(dx * step / steps)
            y = start_y + round(dy * step / steps)
            self.move_to(x, y)
            if duration and step < steps:
                time.sleep(duration / steps)

    def button_down(self, button: str | Any = "LEFT") -> None:
        self.controller.press(self.resolve_button(button))

    def button_up(self, button: str | Any = "LEFT") -> None:
        self.controller.release(self.resolve_button(button))

    def click(self, button: str | Any = "LEFT", clicks: int = 1, interval: float = 0.05) -> None:
        if clicks < 1 or interval < 0:
            raise ValueError("clicks must be positive and interval cannot be negative")
        resolved = self.resolve_button(button)
        for index in range(clicks):
            self.controller.click(resolved)
            if index + 1 < clicks and interval:
                time.sleep(interval)

    def scroll(self, dx: int = 0, dy: int = 0) -> None:
        self.controller.scroll(dx, dy)
