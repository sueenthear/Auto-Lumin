"""Replay version 2 ok-lumin.stepscript recordings."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from .core import ScriptCore

FORMAT = "ok-lumin.stepscript"
VERSION = 2


def load_steps(path: str | Path) -> dict[str, Any]:
    """Load and validate a stepscript recording."""
    with Path(path).open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"Expected a {FORMAT!r} recording")
    if data.get("version") != VERSION:
        raise ValueError(f"Unsupported stepscript version: {data.get('version')!r}")
    if data.get("timestamp_unit") != "relative_ns":
        raise ValueError("Only relative_ns timestamps are supported")
    actions = data.get("actions")
    if not isinstance(actions, list):
        raise ValueError("Stepscript actions must be a list")
    previous = -1
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f"Action {index} must be an object")
        timestamp = action.get("timestamp_ns")
        if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < previous:
            raise ValueError(f"Action {index} has an invalid or decreasing timestamp_ns")
        previous = timestamp
        kind = action.get("type")
        if kind in ("key_down", "key_up"):
            if not isinstance(action.get("key"), str) or not action["key"]:
                raise ValueError(f"Action {index} requires a non-empty key")
        elif kind in ("click", "mouse_down", "mouse_up"):
            if action.get("button") not in ("left", "right", "middle", "x1", "x2"):
                raise ValueError(f"Action {index} has an unsupported mouse button")
        else:
            raise ValueError(f"Action {index} has unsupported type: {kind!r}")
    return data


def replay(
    recording: dict[str, Any] | str | Path,
    core: ScriptCore,
    *,
    speed: float = 1.0,
    activate: bool = True,
) -> None:
    """Replay actions in real time; speed greater than 1 runs proportionally faster."""
    if speed <= 0:
        raise ValueError("speed must be positive")
    data = load_steps(recording) if isinstance(recording, (str, Path)) else recording
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != VERSION:
        raise ValueError("Invalid or unsupported stepscript recording")
    if data.get("timestamp_unit") != "relative_ns" or not isinstance(data.get("actions"), list):
        raise ValueError("Invalid stepscript timing or actions")
    if activate and not core.window.activate():
        raise RuntimeError(f"Could not activate window {core.window.title!r}")

    actions = data["actions"]
    start = time.perf_counter_ns()
    pressed_keys: set[str] = set()
    pressed_buttons: set[str] = set()
    try:
        for action in actions:
            deadline = start + action["timestamp_ns"] / speed
            remaining = deadline - time.perf_counter_ns()
            if remaining > 0:
                time.sleep(remaining / 1_000_000_000)
            kind = action["type"]
            if kind == "key_down":
                key = action["key"]
                core.input.key_down(key)
                pressed_keys.add(key)
            elif kind == "key_up":
                key = action["key"]
                core.input.key_up(key)
                pressed_keys.discard(key)
            elif kind == "mouse_down":
                button = action["button"]
                core.input.mouse_down(button)
                pressed_buttons.add(button)
            elif kind == "mouse_up":
                button = action["button"]
                core.input.mouse_up(button)
                pressed_buttons.discard(button)
            else:
                core.input.click(action["button"])
    finally:
        for key in pressed_keys:
            try:
                core.input.key_up(key)
            except Exception:
                pass
        for button in pressed_buttons:
            try:
                core.input.mouse_up(button)
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay an ok-lumin stepscript recording")
    parser.add_argument("recording", type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--title", help="substring of the target window title")
    target.add_argument("--executable", help="target executable name")
    target.add_argument("--hwnd", type=lambda value: int(value, 0), help="target window handle")
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    args = parser.parse_args()
    core = ScriptCore.bind(title=args.title, executable_name=args.executable, hwnd=args.hwnd)
    replay(args.recording, core, speed=args.speed)


if __name__ == "__main__":
    main()
