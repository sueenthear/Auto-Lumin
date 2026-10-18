from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FORMAT = "ok-lumin.stepscript"
VERSION = 2


def normalize_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate actions, normalize timestamps, and repair held inputs."""
    result: list[dict[str, Any]] = []
    held_keys: dict[str, int] = {}
    held_buttons: dict[str, int] = {}
    previous_ns = 0
    ordered = sorted(actions, key=lambda item: int(item["timestamp_ns"]))
    first_timestamp = int(ordered[0]["timestamp_ns"]) if ordered else 0

    for raw in ordered:
        action = dict(raw)
        kind = action.get("type")
        timestamp = action.get("timestamp_ns")
        if not isinstance(timestamp, int) or timestamp < 0:
            raise ValueError("timestamp_ns must be a non-negative integer")
        timestamp -= first_timestamp
        action["timestamp_ns"] = timestamp
        if timestamp < previous_ns:
            raise ValueError("actions must have monotonic timestamps")
        if kind in ("key_down", "key_up"):
            key = action.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError("keyboard actions require a key")
            if kind == "key_down":
                held_keys[key] = len(result)
            else:
                held_keys.pop(key, None)
        elif kind in ("mouse_down", "mouse_up", "click"):
            button = action.get("button", "left")
            if button not in ("left", "right", "middle", "x1", "x2"):
                raise ValueError(f"unsupported mouse button: {button}")
            action["button"] = button
            if kind == "mouse_down":
                held_buttons[button] = len(result)
            elif kind == "mouse_up":
                held_buttons.pop(button, None)
        else:
            raise ValueError(f"unsupported action type: {kind}")
        previous_ns = timestamp
        result.append(action)

    release_ns = previous_ns + 1
    for key in held_keys:
        result.append({"type": "key_up", "key": key, "timestamp_ns": release_ns, "repaired": True})
        release_ns += 1
    for button in held_buttons:
        result.append({"type": "mouse_up", "button": button, "timestamp_ns": release_ns, "repaired": True})
        release_ns += 1
    return result


def save_script(path: str | Path, actions: list[dict[str, Any]]) -> None:
    normalized = normalize_actions(actions)
    duration_ns = (normalized[-1]["timestamp_ns"] - normalized[0]["timestamp_ns"]
                   if len(normalized) > 1 else 0)
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "timestamp_unit": "relative_ns",
        "info": {"duration_ns": duration_ns, "action_count": len(normalized)},
        "actions": normalized,
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_script(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ValueError("unsupported .stepscript format or version")
    if payload.get("timestamp_unit") != "relative_ns" or not isinstance(payload.get("actions"), list):
        raise ValueError("invalid .stepscript timestamp unit or actions")
    return normalize_actions(payload["actions"])


def action_label(action: dict[str, Any]) -> str:
    kind = action["type"]
    if kind == "key_down":
        return f"按下 {action['key']}"
    if kind == "key_up":
        suffix = "（自动补齐）" if action.get("repaired") else ""
        return f"抬起 {action['key']}{suffix}"
    if kind == "click":
        buttons = {"left": "左键点击", "right": "右键点击", "middle": "中键点击",
                   "x1": "侧键 1 点击", "x2": "侧键 2 点击"}
        return buttons.get(action.get("button", "left"), "鼠标点击")
    if kind == "mouse_down":
        return f"按下鼠标{action.get('button', 'left')}键"
    if kind == "mouse_up":
        suffix = "（自动补齐）" if action.get("repaired") else ""
        return f"抬起鼠标{action.get('button', 'left')}键{suffix}"
    return kind
