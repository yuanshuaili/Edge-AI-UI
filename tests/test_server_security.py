"""Browser-origin, event-stream admission and shutdown safety, no models."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ui_backend.server import create_server


class ServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        profile = {"id": "mock", "name": "示例", "device": "设备", "mode": "Local",
                   "adapter": "mock", "endpoint": "mock://local",
                   "capabilities": {"text": True, "voice": True, "image": False, "video": False, "tts": False},
                   "available_inputs": {"text": True, "voice": True, "image": False, "video": False}}
        self.config = root / "backends.json"
        self.config.write_text(json.dumps({"default_backend_id": "mock", "backends": [profile]}))
        self.assistant = root / "assistant.json"
        self.assistant.write_text(json.dumps({"id": "sample", "name": "助手", "subtitle": "本地",
                                              "welcome": "欢迎", "system_prompt": "中文"}))
        self.server = create_server(self.config, port=0, max_clients=2, assistant_path=self.assistant)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def get(self, path, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            client.request("GET", path, headers=headers or {})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            client.close()

    def stream(self):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        origin = f"http://127.0.0.1:{self.server.server_port}"
        client.request("GET", "/api/voice/events", headers={"Origin": origin})
        response = client.getresponse()
        self.addCleanup(client.close)
        return response

    def test_cross_site_event_streams_are_rejected_even_without_origin(self):
        for headers in ({"Origin": "http://attacker.invalid"},
                        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},
                        {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                # Read only the headers: a vulnerable SSE body does not end.
                client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
                client.request("GET", "/api/voice/events", headers=headers)
                response = client.getresponse()
                try:
                    self.assertEqual(response.status, 403)
                finally:
                    client.close()

    def test_event_stream_pool_preserves_regular_api_capacity(self):
        streams = [self.stream() for _ in range(2)]
        self.assertTrue(all(response.status == 200 for response in streams))
        self.assertEqual(self.get("/api/voice/state")[0], 200)

    def test_event_stream_pool_is_bounded(self):
        streams = [self.stream() for _ in range(4)]
        self.assertEqual([response.status for response in streams], [200] * 4)
        self.assertEqual(self.stream().status, 503)
        self.assertEqual(self.get("/api/backends")[0], 200)

    def test_page_cannot_be_embedded_in_another_origin(self):
        for path in ("/", "/index.html"):
            status, headers, _ = self.get(path)
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Frame-Options"), "DENY")
            self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))

    def test_same_origin_and_cli_requests_remain_supported(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.get("/api/assistant", {"Origin": origin, "Sec-Fetch-Site": "same-origin"})[0], 200)
        self.assertEqual(self.get("/api/assistant")[0], 200)

    def test_shutdown_waits_for_non_http_voice_generation(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        adapter = self.server.model_manager.adapters["mock"]
        def answer(text):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release timed out")
            return "语音答案"
        adapter.text_chat = answer
        voice = self.server.voice
        voice.attach_sender(lambda message: None)
        voice.set_asr_connected(True)
        request_id = voice.start_listening()
        voice.handle_asr_event({"v": 1, "type": "transcript", "request_id": request_id, "text": "你好"})
        self.assertTrue(entered.wait(2))
        with patch.object(self.server.model_manager, "close") as cleanup:
            def close():
                self.server.shutdown()
                self.server.server_close()
                closed.set()
            closer = threading.Thread(target=close)
            closer.start()
            try:
                self.assertFalse(closed.wait(.7), "Model cleanup ran while voice inference was still active")
                cleanup.assert_not_called()
            finally:
                release.set()
                closer.join(3)
            self.assertTrue(closed.is_set())
            cleanup.assert_called_once()
            self.assertFalse(voice.dispatcher.is_busy("mock"))

    def test_bind_failure_preserves_original_error(self):
        with self.assertRaises(OSError) as caught:
            create_server(self.config, port=self.server.server_port, assistant_path=self.assistant)
        self.assertEqual(caught.exception.errno, 98)  # Linux EADDRINUSE

    def test_shutdown_drain_timeout_does_not_kill_busy_model(self):
        entered, release = threading.Event(), threading.Event()
        adapter = self.server.model_manager.adapters["mock"]
        def answer(text):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release timed out")
            return "完成"
        adapter.text_chat = answer
        voice = self.server.voice
        voice.attach_sender(lambda message: None)
        voice.set_asr_connected(True)
        request_id = voice.start_listening()
        voice.handle_asr_event({"v": 1, "type": "transcript", "request_id": request_id, "text": "你好"})
        self.assertTrue(entered.wait(2))
        self.server.shutdown()
        try:
            with patch("ui_backend.server.SHUTDOWN_DRAIN_SECONDS", .05, create=True), \
                 patch.object(self.server.model_manager, "close") as cleanup:
                self.server.server_close()
                cleanup.assert_not_called()
                self.assertTrue(voice.dispatcher.is_busy("mock"))
        finally:
            release.set()

    def test_incomplete_json_body_cannot_execute_chat(self):
        body = b'{"backend_id":"mock","text":"hello"}'
        port = self.server.server_port
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall((f"POST /api/chat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                            f"Content-Type: application/json\r\nContent-Length: {len(body)+20}\r\n\r\n").encode() + body)
            client.shutdown(socket.SHUT_WR)
            response = client.recv(4096)
            self.assertEqual(response.split()[1], b"400", response)
        self.assertFalse(self.server.voice.dispatcher.is_busy("mock"))

    def test_json_body_has_total_deadline_not_only_idle_timeout(self):
        server = create_server(self.config, port=0, client_timeout=.15, assistant_path=self.assistant)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body = b'{"backend_id":"mock","text":"hello"}'
        port = server.server_port
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                client.sendall((f"POST /api/chat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode())
                for byte in body:
                    try:
                        client.sendall(bytes([byte]))
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(.03)  # Every chunk meets idle timeout; total does not.
                response = client.recv(4096)
                self.assertEqual(response.split()[1], b"408", response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_headerless_sse_needs_valid_same_origin_page_token(self):
        # HTTP LAN browsers can omit both Origin and Fetch Metadata, just like
        # no-cors attackers. The page obtains this nonce through same-origin JSON.
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        client.request("GET", "/api/voice/events")
        try:
            self.assertEqual(client.getresponse().status, 403)
        finally:
            client.close()
        self.assertEqual(self.get("/api/voice/events?token=wrong")[0], 403)
        _, _, body = self.get("/api/backends")
        token = json.loads(body)["event_stream_token"]
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        client.request("GET", "/api/voice/events?token=" + token)
        try:
            self.assertEqual(client.getresponse().status, 200)
        finally:
            client.close()
