"""Tests for this.task variable reading and persistence."""
import json
import tempfile
import unittest
from pathlib import Path

from script_core.variables import (
    DEFAULT_DESCRIPTION,
    THIS_TASK_FLAG,
    TaskVariables,
    coerce_value,
    normalize_type,
    task_path_from_argv,
    type_name,
)


class ArgumentTests(unittest.TestCase):
    def test_flag_with_separate_and_inline_path(self):
        self.assertEqual(task_path_from_argv(["--this-task", "a/this.task"]),
                         Path("a/this.task"))
        self.assertEqual(task_path_from_argv(["--this-task=b/this.task"]),
                         Path("b/this.task"))
        self.assertEqual(task_path_from_argv(["--other", "x"]), None)

    def test_missing_path_is_an_error(self):
        for args in (["--this-task"], ["--this-task="]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                task_path_from_argv(args)

    def test_flag_is_read_from_sys_argv_by_default(self):
        import sys
        from unittest.mock import patch
        with patch.object(sys, "argv", ["run.py", THIS_TASK_FLAG, "this.task"]):
            self.assertEqual(task_path_from_argv(), Path("this.task"))


class TypeTests(unittest.TestCase):
    def test_detection_prefers_bool(self):
        self.assertEqual(type_name(True), "bool")
        self.assertEqual(type_name(1), "int")
        self.assertEqual(type_name(1.5), "float")
        self.assertEqual(type_name("x"), "string")
        with self.assertRaises(TypeError):
            type_name(None)

    def test_normalize_accepts_names_types_and_none(self):
        self.assertIsNone(normalize_type(None))
        for value, expected in ((int, "int"), (float, "float"), (str, "string"),
                                (bool, "bool"), ("int", "int"), ("Str", "string"),
                                ("BOOL", "bool")):
            with self.subTest(value=value):
                self.assertEqual(normalize_type(value), expected)
        with self.assertRaises(TypeError):
            normalize_type(list)

    def test_float_accepts_int_but_int_rejects_float_and_bool(self):
        self.assertEqual(coerce_value(3, "float", source="x"), 3.0)
        self.assertIsInstance(coerce_value(3, "float", source="x"), float)
        for value, expected in ((3.5, "int"), (True, "int"), (True, "float"),
                                (1, "bool"), (1, "string")):
            with self.subTest(value=value, expected=expected), \
                 self.assertRaises(TypeError):
                coerce_value(value, expected, source="x")


class TaskVariablesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lumin-vars-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "task" / "this.task"
        self.path.parent.mkdir()

    def write(self, data, *, bom=False):
        text = json.dumps(data, ensure_ascii=False, indent=2)
        self.path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
        return data

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8-sig"))

    def test_without_this_task_returns_defaults_and_writes_nothing(self):
        variables = TaskVariables(argv=[])
        self.assertFalse(variables.enabled)
        self.assertEqual(variables.get("times", 3), 3)
        self.assertFalse(self.path.exists())

    def test_missing_variable_is_persisted_with_type_and_description(self):
        self.write({"title": "示例", "description": "说明"})
        variables = TaskVariables(self.path)
        self.assertEqual(variables.get("times", 50, "执行次数"), 50)
        data = self.stored()
        self.assertEqual(data["title"], "示例")
        self.assertEqual(data["var"]["value"], {"times": 50})
        self.assertEqual(data["var"]["type"], {"times": "int"})
        self.assertEqual(data["var"]["description"], {"times": "执行次数"})

    def test_existing_value_wins_and_is_not_overwritten(self):
        self.write({"var": {
            "value": {"times": 8},
            "type": {"times": "int"},
            "description": {"times": "已有描述"},
        }})
        variables = TaskVariables(self.path)
        self.assertEqual(variables.get("times", 50, "默认描述"), 8)
        data = self.stored()
        self.assertEqual(data["var"]["value"]["times"], 8)
        self.assertEqual(data["var"]["description"]["times"], "已有描述")

    def test_existing_value_gets_missing_metadata_without_overwrite(self):
        self.write({"var": {"value": {"speed": 2}}})
        self.assertEqual(TaskVariables(self.path).get("speed", 1.0, "速度"), 2.0)
        data = self.stored()
        self.assertEqual(data["var"]["value"]["speed"], 2.0)
        self.assertEqual(data["var"]["type"]["speed"], "float")
        self.assertEqual(data["var"]["description"]["speed"], "速度")

    def test_detects_all_supported_types(self):
        variables = TaskVariables(self.path)
        self.write({"var": {"value": {}}})
        self.assertIs(variables.get("flag", True), True)
        self.assertEqual(variables.get("count", 2), 2)
        self.assertEqual(variables.get("ratio", 1.5), 1.5)
        self.assertEqual(variables.get("name", "默认"), "默认")
        data = self.stored()
        self.assertEqual(data["var"]["type"], {
            "flag": "bool", "count": "int", "ratio": "float", "name": "string",
        })

    def test_explicit_type_controls_result(self):
        self.write({"var": {"value": {}}})
        variables = TaskVariables(self.path)
        value = variables.get("ratio", 2, "倍率", type="float")
        self.assertEqual(value, 2.0)
        self.assertIsInstance(value, float)
        self.assertEqual(self.stored()["var"]["type"]["ratio"], "float")
        self.assertEqual(variables.get("name", "x", type=str), "x")

    def test_incompatible_values_and_types_raise(self):
        self.write({"var": {"value": {}}})
        variables = TaskVariables(self.path)
        with self.assertRaises(TypeError):
            variables.get("times", "5", type="int")
        with self.assertRaises(TypeError):
            variables.get("times", 5, type="list")

        self.write({"var": {
            "value": {"times": "5"}, "type": {"times": "int"},
            "description": {"times": "次数"},
        }})
        with self.assertRaises(TypeError):
            TaskVariables(self.path).get("times", 1)

        self.write({"var": {
            "value": {"times": 5}, "type": {"times": "string"},
            "description": {"times": "次数"},
        }})
        with self.assertRaises(TypeError):
            TaskVariables(self.path).get("times", 1)

    def test_bom_is_preserved(self):
        self.write({"title": "BOM"}, bom=True)
        TaskVariables(self.path).get("times", 1)
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(self.stored()["title"], "BOM")

    def test_bad_files_and_sections_raise(self):
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            TaskVariables(self.path).get("x", 1)
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            TaskVariables(self.path).get("x", 1)
        for var in ([], {"value": []}, {"value": {}, "type": []},
                    {"value": {}, "description": []}):
            with self.subTest(var=var):
                self.write({"var": var})
                with self.assertRaises(ValueError):
                    TaskVariables(self.path).get("x", 1)

    def test_bad_arguments_raise(self):
        variables = TaskVariables()
        for name in ("", "  ", None):
            with self.subTest(name=name), self.assertRaises((TypeError, ValueError)):
                variables.get(name, 1)
        with self.assertRaises(TypeError):
            variables.get("x", 1, description=None)
        with self.assertRaises(TypeError):
            variables.get("x", object())

    def test_default_description_is_used_when_omitted(self):
        self.write({"var": {"value": {}}})
        TaskVariables(self.path).get("x", 1)
        self.assertEqual(self.stored()["var"]["description"]["x"], DEFAULT_DESCRIPTION)


if __name__ == "__main__":
    unittest.main()
