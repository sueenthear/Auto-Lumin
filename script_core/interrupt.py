from __future__ import annotations

import os
from typing import Any

from pynput.keyboard import KeyCode, Listener

from .input_manager.keyboard import KeyboardInput


class Interrupt:
    """Exit the process when the configured global key is pressed."""

    def __init__(self) -> None:
        self._key = self._resolve("F12")
        self._listener = Listener(on_press=self._on_press)
        self._listener.start()

    @staticmethod
    def _resolve(key: str | int) -> Any:
        resolved, modifiers = KeyboardInput.resolve(key)
        if modifiers:
            return KeyCode.from_char(key)
        if isinstance(resolved, str):
            return KeyCode.from_char(resolved)
        return resolved

    def set(self, key: str | int) -> None:
        """Set the single key whose press immediately exits the process."""
        self._key = self._resolve(key)

    def _on_press(self, key: Any) -> None:
        if key == self._key:
            os._exit(0)
            return

        virtual_key = getattr(self._key, "vk", None)
        if virtual_key is not None and getattr(key, "vk", None) == virtual_key:
            os._exit(0)
            return

        character = getattr(key, "char", None)
        if character:
            try:
                if self._resolve(character) == self._key:
                    os._exit(0)
            except ValueError:
                pass
