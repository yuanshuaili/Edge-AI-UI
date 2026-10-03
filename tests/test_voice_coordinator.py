import json
import socket
import time
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ui_backend.adapters import MockAdapter
from ui_backend.chat import ChatDispatcher
from ui_backend.config import AppConfig, BackendConfig


def build_coordinator():
    from ui_backend.voice import VoiceCoordinator

    profiles = {}
    for backend_id in ("alpha", "beta"):
        profiles[backend_id] = BackendConfig(
            backend_id, "演示后端", "本地模拟", "Local", "mock", "mock://local",
            {"text": True, "voice": True, "image": False, "video": False, "tts": False},
            {"text": True, "voice": True, "image": False, "video": False}, None,
        )
    config = AppConfig("", "alpha", profiles)
    dispatcher = ChatDispatcher(config, {key: MockAdapter(value) for key, value in profiles.items()})
    coordinator = VoiceCoordinator(config, dispatcher, max_listen_seconds=20)
    commands = []
    coordinator.attach_sender(commands.append)
    coordinator.set_asr_connected(True)
    return coordinator, commands


class VoiceCoordinatorTests(unittest.TestCase):
    def test_last_browser_subscriber_leaving_cancels_active_capture(self):
        coordinator, commands = build_coordinator()
        try:
            coordinator.subscriber_enter()
            request_id = coordinator.start_listening()
            coordinator.subscriber_exit()
            self.assertEqual(coordinator.snapshot()["phase"], "idle")
            self.assertEqual(commands[-1]["type"], "stop_listening")
            coordinator.handle_asr_event({"v": 1, "type": "transcript",
                                          "request_id": request_id, "text": "迟到"})
            self.assertFalse(any(item["type"] == "transcript_ready" for item in coordinator.events_after(0)))
        finally:
            coordinator.close()

    def test_text_chat_busy_state_is_published_for_microphone_gate(self):
        coordinator, _ = build_coordinator()
        entered = threading.Event()
        release = threading.Event()
        adapter = coordinator.dispatcher.adapters["alpha"]
        original = adapter.text_chat

        def slow(text):
            entered.set()
            release.wait(timeout=2)
            return original(text)

        adapter.text_chat = slow
        worker = threading.Thread(target=lambda: coordinator.dispatcher.dispatch_text_chat("alpha", "你好"))
        try:
            worker.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(coordinator.can_start())
            self.assertTrue(any(item["type"] == "backend_busy" and item["busy"]
                                for item in coordinator.events_after(0)))
            release.set()
            worker.join(timeout=1)
            self.assertTrue(any(item["type"] == "backend_busy" and not item["busy"]
                                for item in coordinator.events_after(0)))
        finally:
            release.set()
            worker.join(timeout=1)
            coordinator.close()

    def test_transcript_uses_backend_selected_at_arrival_and_freezes_answer_route(self):
        coordinator, commands = build_coordinator()
        try:
            request_id = coordinator.start_listening()
            self.assertEqual(commands[-1], {"v": 1, "type": "start_listening",
                                            "request_id": request_id, "max_listen_seconds": 20})
            coordinator.select_backend("beta")
            coordinator.handle_asr_event({"v": 1, "type": "transcript",
                                          "request_id": request_id, "text": "你好"})
            coordinator.select_backend("alpha")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                item["type"] == "answered" for item in coordinator.events_after(0)
            ):
                time.sleep(0.01)
            events = coordinator.events_after(0)
            transcript = next(item for item in events if item["type"] == "transcript_ready")
            answer = next(item for item in events if item["type"] == "answered")
            self.assertEqual(transcript["backend_id"], "beta")
            self.assertEqual(answer["backend_id"], "beta")
            self.assertEqual(answer["text"], "演示后端[beta]已收到：你好")
            self.assertLess([item["type"] for item in events].index("thinking"),
                            [item["type"] for item in events].index("answered"))
        finally:
            coordinator.close()

    def test_cancel_discards_late_transcript_and_sends_stop(self):
        coordinator, commands = build_coordinator()
        try:
            request_id = coordinator.start_listening()
            coordinator.cancel_listening()
            coordinator.handle_asr_event({"v": 1, "type": "transcript",
                                          "request_id": request_id, "text": "迟到的提问"})
            self.assertEqual(commands[-1]["type"], "stop_listening")
            self.assertEqual(coordinator.snapshot()["phase"], "idle")
            self.assertNotIn("transcript_ready", [item["type"] for item in coordinator.events_after(0)])
        finally:
            coordinator.close()

    def test_no_speech_is_neutral_error_without_model_call(self):
        coordinator, _ = build_coordinator()
        try:
            request_id = coordinator.start_listening()
            coordinator.handle_asr_event({"v": 1, "type": "error", "request_id": request_id,
                                          "code": "no_speech"})
            self.assertEqual(coordinator.snapshot()["phase"], "idle")
            self.assertEqual(coordinator.events_after(0)[-1]["message"],
                             "未检测到有效语音，请重试")
        finally:
            coordinator.close()

    def test_asr_disconnect_disables_new_listening(self):
        from ui_backend.voice import ASRUnavailable

        coordinator, _ = build_coordinator()
        try:
            coordinator.set_asr_connected(False)
            self.assertFalse(coordinator.snapshot()["asr_connected"])
            with self.assertRaises(ASRUnavailable):
                coordinator.start_listening()
        finally:
            coordinator.close()


class VoiceProtocolTests(unittest.TestCase):
    def test_old_asr_reader_cannot_mark_new_connection_offline(self):
        from ui_backend.voice import UnixAsrBridge

        coordinator, _ = build_coordinator()
        old_client, old_peer = socket.socketpair()
        new_client, new_peer = socket.socketpair()
        try:
            bridge = UnixAsrBridge("/tmp/not-created.sock", coordinator)
            bridge._client = new_client
            old_peer.close()
            bridge._read_loop(old_client)
            self.assertTrue(coordinator.snapshot()["asr_connected"])
        finally:
            new_client.close()
            new_peer.close()
            coordinator.close()

    def test_bool_version_is_not_accepted(self):
        from ui_backend.voice_protocol import ProtocolError, validate_message

        with self.assertRaises(ProtocolError):
            validate_message({"v": True, "type": "idle", "request_id": "r1"})

    def test_future_event_id_gets_snapshot_after_server_restart(self):
        coordinator, _ = build_coordinator()
        try:
            self.assertEqual(coordinator.events_after(999)[0]["type"], "state")
            self.assertEqual(coordinator.events_after(999)[0]["epoch"], coordinator.snapshot()["epoch"])
        finally:
            coordinator.close()

    def test_bridge_shutdown_does_not_crash_reader_thread(self):
        from ui_backend.voice import UnixAsrBridge

        coordinator, _ = build_coordinator()
        coordinator.set_asr_connected(False)
        failures = []
        with TemporaryDirectory() as folder:
            bridge = UnixAsrBridge(Path(folder) / "asr.sock", coordinator)
            bridge.start()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(bridge.path))
            deadline = time.monotonic() + 1
            while not coordinator.snapshot()["asr_connected"] and time.monotonic() < deadline:
                time.sleep(0.01)
            with patch.object(threading, "excepthook", lambda args: failures.append(args.exc_value)):
                bridge.close()
                time.sleep(0.1)
            client.close()
            coordinator.close()
        self.assertEqual(failures, [])

    def test_rejects_malformed_utf8_json_and_oversize_messages(self):
        from ui_backend.voice_protocol import MAX_MESSAGE_BYTES, ProtocolError, decode_message

        for payload in (b"\xff\n", b"not json\n", b"{\"v\":2,\"type\":\"transcript\"}\n",
                        b"x" * (MAX_MESSAGE_BYTES + 1)):
            with self.subTest(payload=payload[:20]), self.assertRaises(ProtocolError):
                decode_message(payload)

    def test_unix_bridge_rejects_second_asr_without_replacing_first(self):
        from ui_backend.voice import UnixAsrBridge

        coordinator, _ = build_coordinator()
        coordinator.set_asr_connected(False)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "asr.sock"
            bridge = UnixAsrBridge(path, coordinator)
            bridge.start()
            first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                first.connect(str(path))
                deadline = time.monotonic() + 1
                while not coordinator.snapshot()["asr_connected"] and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(coordinator.snapshot()["asr_connected"])
                second.connect(str(path))
                time.sleep(0.1)
                self.assertTrue(coordinator.snapshot()["asr_connected"])
                request_id = coordinator.start_listening()
                first.settimeout(1)
                message = json.loads(first.recv(4096).split(b"\n")[0])
                self.assertEqual(message["request_id"], request_id)
            finally:
                first.close()
                second.close()
                bridge.close()
                coordinator.close()
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
