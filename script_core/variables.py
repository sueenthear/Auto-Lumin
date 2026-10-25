"""Read and persist script variables stored in ``this.task``.

启动器通过 ``--this-task <path>`` 把当前任务的 ``this.task`` 路径传给脚本。
``Core.var(...)`` 使用这里的实现：优先返回 ``this.task`` 中已有的值，缺失时
写入默认值；没有指定文件路径时只返回默认值，不会改动任何文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

THIS_TASK_FLAG = "--this-task"
DEFAULT_DESCRIPTION = "这是一个可定义的变量"
TYPE_NAMES = ("int", "float", "string", "bool")

_TYPE_ALIASES = {
    "int": "int",
    "float": "float",
    "str": "string",
    "string": "string",
    "bool": "bool",
}


def task_path_from_argv(argv: list[str] | None = None) -> Path | None:
    """Return the ``--this-task`` path from command line arguments, if any."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    for index, argument in enumerate(arguments):
        if argument == THIS_TASK_FLAG:
            if index + 1 >= len(arguments):
                raise ValueError(f"{THIS_TASK_FLAG} requires a path")
            return Path(arguments[index + 1]).expanduser()
        if argument.startswith(THIS_TASK_FLAG + "="):
            value = argument.split("=", 1)[1]
            if not value:
                raise ValueError(f"{THIS_TASK_FLAG} requires a path")
            return Path(value).expanduser()
    return None


def type_name(value: Any) -> str:
    """Return the supported variable type name for a Python value."""
    # bool 是 int 的子类，必须先判断。
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    raise TypeError(
        f"variable value must be int, float, string or bool, not {type(value).__name__}"
    )


def normalize_type(value: Any) -> str | None:
    """Normalize a type given as a Python type, a name, or ``None``."""
    if value is None:
        return None
    if value is bool:
        return "bool"
    if value is int:
        return "int"
    if value is float:
        return "float"
    if value is str:
        return "string"
    if isinstance(value, str):
        name = _TYPE_ALIASES.get(value.strip().lower())
        if name is not None:
            return name
    raise TypeError("variable type must be int, float, string, bool or None")


def coerce_value(value: Any, expected: str, *, source: str) -> Any:
    """Validate ``value`` against ``expected``; int is accepted for float."""
    actual = type_name(value)
    if actual == expected:
        return value
    if expected == "float" and actual == "int":
        return float(value)
    raise TypeError(f"{source} must be {expected}, not {actual}")


class TaskVariables:
    """A lazy view over the ``var`` section of one ``this.task`` file."""

    def __init__(self, path: str | Path | None = None, argv: list[str] | None = None):
        if path is None:
            path = task_path_from_argv(argv)
        self.path = Path(path).expanduser().resolve() if path is not None else None
        self._data: dict[str, Any] | None = None
        self._bom = False

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def get(
        self,
        name: str,
        value: Any,
        description: str = DEFAULT_DESCRIPTION,
        type: Any = None,
    ) -> Any:
        """Return the stored value, or persist and return the default value."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("variable name must be a non-empty string")
        if not isinstance(description, str):
            raise TypeError("variable description must be a string")
        expected = normalize_type(type) or type_name(value)
        default = coerce_value(value, expected, source=f'default value of "{name}"')

        self._load()
        if self.path is None or self._data is None:
            return default

        var = self._data.get("var")
        if var is None:
            var = {}
            self._data["var"] = var
        if not isinstance(var, dict):
            raise ValueError(f'"var" in {self.path} must be an object')

        values = var.get("value", {})
        types = var.get("type", {})
        descriptions = var.get("description", {})
        for key, section in (("value", values), ("type", types),
                             ("description", descriptions)):
            if not isinstance(section, dict):
                raise ValueError(f'"var.{key}" in {self.path} must be an object')

        stored_type = types.get(name)
        if stored_type is not None:
            stored = normalize_type(stored_type)
            if stored != expected:
                raise TypeError(
                    f'variable "{name}" has type {stored} in this.task, '
                    f"but the script defines {expected}"
                )

        changed = False
        if name not in values:
            values[name] = default
            types[name] = expected
            descriptions[name] = description
            changed = True
        else:
            current = coerce_value(
                values[name], expected, source=f'this.task value of "{name}"'
            )
            if type_name(values[name]) != expected:
                values[name] = current
                changed = True
            if name not in types:
                types[name] = expected
                changed = True
            if name not in descriptions:
                descriptions[name] = description
                changed = True

        if changed:
            var["value"] = values
            var["type"] = types
            var["description"] = descriptions
            self._save()
        return values[name]

    def _load(self) -> None:
        if self._data is not None or self.path is None:
            return
        if not self.path.is_file():
            raise FileNotFoundError(f"this.task does not exist: {self.path}")
        raw = self.path.read_bytes()
        self._bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} must contain a JSON object")
        self._data = data

    def _save(self) -> None:
        if self.path is None or self._data is None:
            return
        text = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        self.path.write_text(text, encoding="utf-8-sig" if self._bom else "utf-8")
