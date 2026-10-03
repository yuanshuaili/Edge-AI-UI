import threading
import unittest

from ui_backend.adapters import MockAdapter
from ui_backend.config import AppConfig, BackendConfig


def mock_profile(backend_id):
    return BackendConfig(
        backend_id, "演示后端", "本地模拟", "Local", "mock", "mock://local",
        {"text": True, "voice": True, "image": False, "video": False, "tts": False},
        {"text": True, "voice": True, "image": False, "video": False}, None,
    )


class ChatDispatcherTests(unittest.TestCase):
    def test_explicit_backend_id_routes_to_selected_adapter(self):
        from ui_backend.chat import ChatDispatcher

        profiles = {key: mock_profile(key) for key in ("alpha", "beta")}
        dispatcher = ChatDispatcher(AppConfig("", "alpha", profiles),
                                    {key: MockAdapter(item) for key, item in profiles.items()})
        result = dispatcher.dispatch_text_chat("beta", "你好")
        self.assertEqual(result.backend_id, "beta")
        self.assertEqual(result.text, "演示后端[beta]已收到：你好")
        self.assertGreaterEqual(result.latency_ms, 0)
        self.assertFalse(dispatcher.is_busy("beta"))

    def test_same_backend_refuses_concurrent_requests_without_queueing(self):
        from ui_backend.chat import BackendBusy, ChatDispatcher

        started = threading.Event()
        release = threading.Event()

        class WaitingMock(MockAdapter):
            def text_chat(self, text):
                started.set()
                release.wait(timeout=2)
                return super().text_chat(text)

        item = mock_profile("alpha")
        dispatcher = ChatDispatcher(AppConfig("", "alpha", {"alpha": item}),
                                    {"alpha": WaitingMock(item)})
        result = []
        worker = threading.Thread(target=lambda: result.append(dispatcher.dispatch_text_chat("alpha", "第一问")))
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=1))
            self.assertTrue(dispatcher.is_busy("alpha"))
            with self.assertRaises(BackendBusy):
                dispatcher.dispatch_text_chat("alpha", "第二问")
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertEqual(result[0].text, "演示后端[alpha]已收到：第一问")
        self.assertFalse(dispatcher.is_busy("alpha"))

    def test_unknown_and_unavailable_text_inputs_are_rejected(self):
        from dataclasses import replace
        from ui_backend.chat import ChatDispatcher, UnsupportedTextInput

        item = mock_profile("alpha")
        no_text = replace(item, id="disabled",
                          available_inputs={"text": False, "voice": False, "image": False, "video": False})
        profiles = {"alpha": item, "disabled": no_text}
        dispatcher = ChatDispatcher(AppConfig("", "alpha", profiles),
                                    {key: MockAdapter(value) for key, value in profiles.items()})
        with self.assertRaises(KeyError):
            dispatcher.dispatch_text_chat("missing", "你好")
        with self.assertRaises(UnsupportedTextInput):
            dispatcher.dispatch_text_chat("disabled", "你好")


if __name__ == "__main__":
    unittest.main()
