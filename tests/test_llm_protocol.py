"""Generic prompt reset tests require no TinyChat installation."""
import unittest
from ui_backend.llm_protocol import ConversationState


class ConversationProtocolTests(unittest.TestCase):
    def test_reset_restores_system_and_removes_old_history_and_cache_position(self):
        class Prompt:
            template = "system: identity {literal}; user: {prompt}"
            model_input = None
        prompt = Prompt()
        state = ConversationState(prompt)
        prompt.template = "old user + answer; next: {prompt}"
        prompt.model_input = "old user"
        state.start_pos = 47
        self.assertEqual(state.control({"action": "clear_session"}), {"ok": True, "action": "clear_session"})
        self.assertEqual(prompt.template, "system: identity {literal}; user: {prompt}")
        self.assertIsNone(prompt.model_input)
        self.assertEqual(state.start_pos, 0)

    def test_legacy_text_is_not_control_and_unknown_action_does_not_clear(self):
        class Prompt:
            template = "identity; {prompt}"
            model_input = "previous input"
        prompt = Prompt()
        state = ConversationState(prompt)
        self.assertIsNone(state.control({"text": "你好"}))
        self.assertFalse(state.control({"action": "shell", "command": "bad"})["ok"])
        self.assertEqual(prompt.model_input, "previous input")
