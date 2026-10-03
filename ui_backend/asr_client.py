"""Lightweight ASR-side client for the UI backend's local Unix socket."""

import select
import socket

from .voice_protocol import MAX_MESSAGE_BYTES, ProtocolError, decode_message, encode_message


class AsrUiClient:
    def __init__(self, path):
        self.path = str(path)
        self._socket = None
        self._buffer = bytearray()

    def connect(self):
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._socket.connect(self.path)
        except OSError:
            self.close()
            raise

    def send(self, message):
        if self._socket is None:
            raise ConnectionError("ASR UI socket is disconnected")
        self._socket.sendall(encode_message(message))

    def read_command(self, timeout=0):
        if self._socket is None:
            raise ConnectionError("ASR UI socket is disconnected")
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                frame = bytes(self._buffer[:newline + 1])
                del self._buffer[:newline + 1]
                command = decode_message(frame)
                if command["type"] not in ("start_listening", "stop_listening", "status"):
                    raise ProtocolError("Unexpected UI command")
                return command
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise ProtocolError("Voice command too large")
            ready, _, _ = select.select([self._socket], [], [], timeout)
            if not ready:
                return None
            data = self._socket.recv(4096)
            if not data:
                raise ConnectionError("UI socket closed")
            self._buffer.extend(data)
            timeout = 0

    def close(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._buffer.clear()
