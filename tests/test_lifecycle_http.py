"""Safe lifecycle HTTP/SSE integration uses Mock only."""
import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ui_backend.server import create_server


class LifecycleHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        profile = {"id": "a", "name": "模拟模型", "device": "示例设备", "mode": "Local",
                   "adapter": "mock", "endpoint": "mock://local",
                   "capabilities": {"text": True, "voice": True, "image": False, "video": False, "tts": False},
                   "available_inputs": {"text": True, "voice": True, "image": False, "video": False}}
        self.config = directory / "backends.json"
        self.config.write_text(json.dumps({"default_backend_id": "a", "backends": [profile, dict(profile, id="b")]}))
        assistant = directory / "assistant.json"
        assistant.write_text(json.dumps({"id": "a", "name": "助手", "subtitle": "本地", "welcome": "欢迎", "system_prompt": "保持身份"}))
        self.server = create_server(self.config, port=0, assistant_path=assistant)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def cleanup_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, data=None):
        req = Request(self.base + path, data=None if data is None else json.dumps(data).encode(),
                      headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=2)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_activate_mock_routes_chat_and_emits_state(self):
        status, result = self.request("/api/model/activate", {"backend_id": "b"})
        self.assertEqual(status, 202)
        self.server.model_manager.wait(2)
        _, state = self.request("/api/model/state")
        self.assertEqual(state["selected_backend_id"], "b")
        self.assertEqual(state["state"], "ready")
        _, answer = self.request("/api/chat", {"backend_id": "b", "text": "你好"})
        self.assertIn("[b]", answer["text"])
        status, cleared = self.request("/api/session/clear", {"backend_id": "b"})
        self.assertEqual(status, 200)
        self.assertTrue(cleared["ok"])
        self.assertTrue(any(event["type"] == "model_state" for event in self.server.voice.events_after(0)))

    def test_commands_are_rejected_and_transition_blocks_chat_clear_voice(self):
        self.assertEqual(self.request("/api/model/activate", {"backend_id": "b", "command": "bad"})[0], 400)
        self.assertEqual(self.request("/api/model/activate", {"backend_id": "unknown"})[0], 404)
        self.server.model_manager.dispatcher.gate.begin_transition()
        try:
            for path, payload in (("/api/chat", {"backend_id": "a", "text": "no"}),
                                  ("/api/session/clear", {"backend_id": "a"}),
                                  ("/api/model/activate", {"backend_id": "b"})):
                self.assertEqual(self.request(path, payload)[0], 409)
            self.assertFalse(self.request("/api/voice/state")[1]["can_listen"])
        finally:
            self.server.model_manager.dispatcher.gate.end_transition()

    def test_activation_cancels_capture_and_discards_late_transcript(self):
        voice = self.server.voice
        sent = []
        voice.attach_sender(sent.append)
        voice.set_asr_connected(True)
        request_id = voice.start_listening()
        self.request("/api/model/activate", {"backend_id": "b"})
        self.server.model_manager.wait(2)
        voice.handle_asr_event({"v": 1, "type": "transcript", "request_id": request_id, "text": "迟到"})
        self.assertEqual(sent[-1]["type"], "stop_listening")
        self.assertFalse(any(event["type"] == "transcript_ready" for event in voice.events_after(0)))

    def test_configured_auto_start_and_no_start_mode(self):
        self.assertIsNone(self.server.model_manager._future)
        self.cleanup_server()
        data = json.loads(self.config.read_text())
        data["backends"][0]["runtime"] = {"managed": True, "launcher": "tinychat_qwen25", "auto_start": True}
        self.config.write_text(json.dumps(data))
        # Ready Mock is a safe stand-in for a previously launched external service.
        self.server = create_server(self.config, port=0, assistant_path=Path(self.temp.name) / "assistant.json")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.server.model_manager.wait(2)
        self.assertEqual(self.server.model_manager.snapshot()["state"], "ready")
        self.assertFalse(self.server.model_manager.snapshot()["owned"])

    def test_reset_admission_blocks_new_capture_before_adapter_reset_starts(self):
        from ui_backend.voice import VoiceUnavailable
        voice = self.server.voice
        voice.attach_sender(lambda command: None)
        voice.set_asr_connected(True)
        dispatcher = self.server.model_manager.dispatcher
        original = dispatcher.clear_session
        entered, release = threading.Event(), threading.Event()
        def delayed_clear(backend_id):
            entered.set()
            release.wait(2)
            return original(backend_id)
        dispatcher.clear_session = delayed_clear
        results = []
        thread = threading.Thread(target=lambda: results.append(self.request("/api/session/clear", {"backend_id": "a"})))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(VoiceUnavailable):
                voice.start_listening()
        finally:
            release.set()
            thread.join(2)
        self.assertEqual(results[0][0], 200)
