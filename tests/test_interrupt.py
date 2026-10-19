import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pynput.keyboard import Key, KeyCode

from script_core.facade import AutomationCore
from script_core.interrupt import Interrupt


class InterruptTests(unittest.TestCase):
    def test_automation_core_starts_default_interrupt_listener(self):
        interrupt = Mock()
        core = SimpleNamespace(window=SimpleNamespace(hwnd=1), image=Mock(), input=Mock())

        with patch("script_core.facade.Interrupt", return_value=interrupt) as create_interrupt:
            automation = AutomationCore(core)

        self.assertIs(automation.interrupt, interrupt)
        create_interrupt.assert_called_once_with()

    def test_starts_global_listener_with_f12_default(self):
        listener = Mock()
        with patch("script_core.interrupt.Listener", return_value=listener) as create_listener:
            interrupt = Interrupt()

        self.assertEqual(interrupt._key, Key.f12)
        create_listener.assert_called_once()
        listener.start.assert_called_once_with()

    def test_configured_key_exits_process_on_key_down(self):
        interrupt = Interrupt.__new__(Interrupt)
        interrupt._key = Key.f12

        with patch("script_core.interrupt.os._exit") as exit_process:
            interrupt._on_press(Key.f11)
            exit_process.assert_not_called()
            interrupt._on_press(Key.f12)

        exit_process.assert_called_once_with(0)

    def test_custom_character_key_exits_process_when_pressed(self):
        interrupt = Interrupt.__new__(Interrupt)
        interrupt._key = Key.f12
        interrupt.set("X")

        with patch("script_core.interrupt.os._exit") as exit_process:
            interrupt._on_press(KeyCode.from_char("X"))

        exit_process.assert_called_once_with(0)

    def test_virtual_key_code_matches_key_event(self):
        interrupt = Interrupt.__new__(Interrupt)
        interrupt.set(0x41)
        event = KeyCode.from_vk(0x41, char="a")

        with patch("script_core.interrupt.os._exit") as exit_process:
            interrupt._on_press(event)

        exit_process.assert_called_once_with(0)

    def test_set_replaces_key_and_rejects_unknown_key_without_changing_it(self):
        interrupt = Interrupt.__new__(Interrupt)
        interrupt._key = Key.f12

        interrupt.set("F10")
        self.assertEqual(interrupt._key, Key.f10)
        interrupt.set("x")
        self.assertEqual(interrupt._key, KeyCode.from_char("x"))

        with self.assertRaises(ValueError):
            interrupt.set("unknown")
        self.assertEqual(interrupt._key, KeyCode.from_char("x"))


if __name__ == "__main__":
    unittest.main()
