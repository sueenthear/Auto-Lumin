from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .facade import AutomationCore

from .image_manager import ImageManager
from .input_manager import InputManager
from .window_manager import WindowManager


@dataclass
class ScriptCore:
    """Bind a game window once and expose its window, input, and image APIs."""

    window: WindowManager
    input: InputManager
    image: ImageManager

    @classmethod
    def bind(
        cls,
        *,
        executable_name: str | None = None,
        title: str | None = None,
        hwnd: int | None = None,
        timeout: float = 0,
    ) -> ScriptCore:
        if timeout < 0:
            raise ValueError("timeout cannot be negative")
        deadline = time.monotonic() + timeout
        while True:
            try:
                window = WindowManager(
                    executable_name=executable_name,
                    title=title,
                    hwnd=hwnd,
                )
                break
            except LookupError:
                if timeout == 0 or time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.2, deadline - time.monotonic()))
        return cls(
            window=window,
            input=InputManager(window),
            image=ImageManager(window),
        )


def Core(
    executable_name: str,
    timeout: float = 10,
    *,
    this_task: str | Path | None = None,
) -> AutomationCore:
    """Bind the target window or raise when it cannot be found in time.

    ``this_task`` is normally supplied by the launcher as ``--this-task <path>``;
    passing it explicitly is useful for tests and manual runs.
    """
    from .facade import AutomationCore

    try:
        return AutomationCore(
            ScriptCore.bind(executable_name=executable_name, timeout=timeout),
            this_task=this_task,
        )
    except LookupError as exc:
        raise SystemExit(f"Could not bind window for {executable_name!r}: {exc}") from exc
