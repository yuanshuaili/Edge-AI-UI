import socket
import unittest
from unittest.mock import patch

from ui_backend.asr_client import AsrUiClient
from ui_backend.voice_protocol import encode_message


class AsrUiClientTests(unittest.TestCase):
    def test_reads_json_lines_and_detects_disconnect(self):
        left, right = socket.socketpair()
        client = AsrUiClient("/unused")
        client._socket = left
        try:
            self.assertIsNone(client.read_command(timeout=0))
            right.sendall(encode_message({"v": 1, "type": "stop_listening", "request_id": "r1"}))
            self.assertEqual(client.read_command(timeout=1)["type"], "stop_listening")
            right.close()
            with self.assertRaises(ConnectionError):
                client.read_command(timeout=1)
        finally:
            client.close()
            right.close()

    def test_sends_framed_event(self):
        left, right = socket.socketpair()
        client = AsrUiClient("/unused")
        client._socket = left
        try:
            client.send({"v": 1, "type": "listening", "request_id": "r1"})
            self.assertIn(b'"listening"', right.recv(1024))
        finally:
            client.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
