from __future__ import annotations

import time
from typing import Any

from pynput.keyboard import Controller, Key, KeyCode


class KeyboardInput:
    MODIFIERS = {
        "SHIFT": Key.shift,
        "CTRL": Key.ctrl,
        "ALT": Key.alt,
        "ALTGR": Key.alt_gr,
        "WIN": Key.cmd,
    }
    NAMED_KEYS = {
        "ENTER": Key.enter,
        "TAB": Key.tab,
        "SPACE": Key.space,
        "BACKSPACE": Key.backspace,
        "ESC": Key.esc,
        "DELETE": Key.delete,
        "INSERT": Key.insert,
        "HOME": Key.home,
        "END": Key.end,
        "PAGEUP": Key.page_up,
        "PAGEDOWN": Key.page_down,
        "LEFT": Key.left,
        "UP": Key.up,
        "RIGHT": Key.right,
        "DOWN": Key.down,
        "CAPSLOCK": Key.caps_lock,
    }
    SHIFTED_CHARACTERS = {
        "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
        "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
        "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\",
        ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
    }

    def __init__(self, controller: Controller | None = None) -> None:
        self.controller = controller or Controller()

    @classmethod
    def resolve(cls, key: str | int | Any) -> tuple[Any, tuple[Any, ...]]:
        if isinstance(key, int):
            return KeyCode.from_vk(key), ()
        if not isinstance(key, str) or not key:
            raise ValueError("Key must be a non-empty name, character, or virtual-key code")

        name = key.upper()
        if name in cls.MODIFIERS:
            return cls.MODIFIERS[name], ()
        if name in cls.NAMED_KEYS:
            return cls.NAMED_KEYS[name], ()
        if len(key) == 1 and key.isascii():
            if key.isalpha() and key.isupper():
                return key.lower(), (Key.shift,)
            if key in cls.SHIFTED_CHARACTERS:
                return cls.SHIFTED_CHARACTERS[key], (Key.shift,)
            return key, ()
        if len(name) >= 2 and name[0] == "F" and name[1:].isdigit():
            function_key = getattr(Key, f"f{int(name[1:])}", None)
            if function_key is not None:
                return function_key, ()
        raise ValueError(f"Unsupported key: {key!r}")

    def key_down(self, key: str | int | Any) -> None:
        resolved, modifiers = self.resolve(key)
        for modifier in modifiers:
            self.controller.press(modifier)
        self.controller.press(resolved)

    def key_up(self, key: str | int | Any) -> None:
        resolved, modifiers = self.resolve(key)
        self.controller.release(resolved)
        for modifier in reversed(modifiers):
            self.controller.release(modifier)

    def press(self, key: str | int | Any, down_time: float = 0.02) -> None:
        if down_time < 0:
            raise ValueError("down_time cannot be negative")
        self.key_down(key)
        try:
            if down_time:
                time.sleep(down_time)
        finally:
            self.key_up(key)

    def press_character(self, character: str, down_time: float = 0.02) -> None:
        if len(character) != 1:
            raise ValueError("Expected exactly one character")
        self.press(character, down_time=down_time)

    def type_text(self, text: str, interval: float = 0.0) -> None:
        if interval < 0:
            raise ValueError("interval cannot be negative")
        self.controller.type(text, interval=interval)
