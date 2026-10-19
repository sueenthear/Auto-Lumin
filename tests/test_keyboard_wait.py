import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from script_core.facade import AutomationCore, _Keyboard, _Mouse
from script_core.input_manager.keyboard import KeyboardInput


class KeyboardFacadeTests(unittest.TestCase):
    def test_methods_forward_explicit_keys_and_text(self):
        input_manager = Mock()
        keyboard = _Keyboard(SimpleNamespace(input=input_manager))
        keyboard.click("?")
        input_manager.press_key.assert_called_with("?", 0.02)
        keyboard.click("\\", down_time=0.1)
        input_manager.press_key.assert_called_with("\\", 0.1)
        keyboard.down("CTRL")
        input_manager.key_down.assert_called_once_with("CTRL")
        keyboard.up("CTRL")
        input_manager.key_up.assert_called_once_with("CTRL")
        keyboard.text("中文", interval=0.1)
        input_manager.type_text.assert_called_once_with("中文", 0.1)
        self.assertFalse(hasattr(keyboard, "shift"))

    def test_key_parser_accepts_special_characters_and_names(self):
        from pynput.keyboard import Key

        self.assertEqual(KeyboardInput.resolve("?"), ("/", (Key.shift,)))
        self.assertEqual(KeyboardInput.resolve("\\"), ("\\", ()))
        self.assertEqual(KeyboardInput.resolve("A"), ("a", (Key.shift,)))
        self.assertEqual(KeyboardInput.resolve("ctrl"), (Key.ctrl, ()))
        self.assertEqual(KeyboardInput.resolve("F20"), (Key.f20, ()))
        with self.assertRaises(ValueError):
            KeyboardInput.resolve("unknown")


class MouseFacadeTests(unittest.TestCase):
    def test_mouse_action_properties_execute_and_move_defaults_apply(self):
        input_manager = Mock()
        mouse = _Mouse(SimpleNamespace(input=input_manager))

        mouse.click.left
        mouse.click.right
        mouse.click.middle
        mouse.click.xfront
        mouse.click.xbehind
        self.assertEqual(
            [call.args[0] for call in input_manager.click.call_args_list],
            ["left", "right", "middle", "xfront", "xbehind"],
        )

        mouse.down.left
        mouse.up.xbehind
        input_manager.mouse_down.assert_called_once_with("left")
        input_manager.mouse_up.assert_called_once_with("xbehind")
        mouse.roll.up
        mouse.roll.down
        self.assertEqual(
            [call.kwargs["dy"] for call in input_manager.scroll.call_args_list],
            [1, -1],
        )

        mouse.set.abs("fps", 30)
        mouse.set.rel("duration", 0.2)
        mouse.move.abs(10, 20)
        mouse.move.rel(1, 2)
        input_manager.move_to.assert_called_once_with(10, 20, duration=0.5, fps=30)
        input_manager.move_by.assert_called_once_with(1, 2, duration=0.2, fps=60)


class ThisFileTests(unittest.TestCase):
    def test_property_changes_directory_to_the_calling_file_directory(self):
        from pathlib import Path

        core = AutomationCore.__new__(AutomationCore)
        caller_file = Path(__file__).resolve().parent / "scripts" / "run.py"
        caller_frame = SimpleNamespace(
            f_code=SimpleNamespace(co_filename=str(caller_file))
        )

        with (
            patch("script_core.facade.sys._getframe", return_value=caller_frame) as getframe,
            patch("script_core.facade.os.chdir") as chdir,
        ):
            result = core.thisfile

        self.assertIsNone(result)
        getframe.assert_called_once_with(1)
        chdir.assert_called_once_with(caller_file.parent)
        self.assertIsInstance(AutomationCore.thisfile, property)


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.core = AutomationCore.__new__(AutomationCore)

    def test_wait_calls_sleep_in_seconds(self):
        with patch("script_core.facade.time.sleep") as sleep:
            self.assertIsNone(self.core.wait(0.25))
            self.core.wait(0)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0])

    def test_wait_rejects_invalid_values(self):
        with patch("script_core.facade.time.sleep") as sleep:
            for value in (-1, float("nan"), float("inf"), True, "1", None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.core.wait(value)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
