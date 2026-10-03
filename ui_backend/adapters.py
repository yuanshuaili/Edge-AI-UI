"""Transport adapters: model-specific protocols stay out of HTTP and JS."""

import json
import socket
from urllib.parse import urlsplit

from .adapter_base import BackendAdapter, HealthStatus


class BackendError(Exception):
    """Base error for an inference backend."""


class BackendUnavailable(BackendError):
    """No connection to the configured backend."""


class BackendTimeout(BackendError):
    """Backend did not finish within the configured timeout."""


class BackendProtocolError(BackendError):
    """Backend returned a response outside its protocol."""


class BackendRejected(BackendError):
    """Backend returned a structured error."""


class TinyChatAdapter(BackendAdapter):
    """One prompt and one answer per UTF-8 JSON-lines TCP connection."""

    MAX_REPLY_BYTES = 1_048_576

    def __init__(self, profile, connect_timeout=5.0, read_timeout=120.0):
        super().__init__(profile)
        parsed = urlsplit(profile.endpoint)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Invalid TinyChat endpoint") from exc
        if parsed.scheme != "tcp" or not parsed.hostname or port is None or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("Invalid TinyChat endpoint")
        self.host = parsed.hostname
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def health(self) -> HealthStatus:
        try:
            with socket.create_connection((self.host, self.port), timeout=min(self.connect_timeout, 0.5)):
                return HealthStatus("ready")
        except OSError:
            return HealthStatus("offline")

    def reachable(self):
        return self.health().state == "ready"

    def endpoint_occupancy(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=min(self.connect_timeout, .5)):
                return "occupied"
        except ConnectionRefusedError:
            return "absent"
        except OSError:
            return "unknown"  # Timeout, permissions, network failure are not absence.

    def text_chat(self, text: str) -> str:
        response = self._exchange({"text": text})
        if not isinstance(response.get("text"), str):
            raise BackendProtocolError("模型服务未返回文本回答。")
        return response["text"]

    def clear_session(self):
        response = self._exchange({"action": "clear_session"})
        if response.get("action") != "clear_session":
            raise BackendProtocolError("模型服务尚未支持新对话。")
        return {"ok": True}

    def _exchange(self, request):
        payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.connect_timeout) as sock:
                sock.settimeout(self.read_timeout)
                with sock.makefile("rwb") as stream:
                    stream.write(payload)
                    stream.flush()
                    line = stream.readline(self.MAX_REPLY_BYTES + 1)
        except socket.timeout as exc:
            raise BackendTimeout("模型响应超时，请稍后重试。") from exc
        except OSError as exc:
            raise BackendUnavailable("模型服务暂时无法连接，请检查服务状态。") from exc

        if not line or len(line) > self.MAX_REPLY_BYTES or not line.endswith(b"\n"):
            raise BackendProtocolError("模型服务返回了不完整的回答。")
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackendProtocolError("模型服务返回了无法解析的回答。") from exc
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise BackendProtocolError("模型服务返回了无效的协议数据。")
        if not response["ok"]:
            raise BackendRejected(str(response.get("error") or "模型拒绝了本次请求。"))
        return response

    def ask(self, text):
        return self.text_chat(text)


class MockAdapter(BackendAdapter):
    """Deterministic demonstration backend; never imports a model."""

    def __init__(self, profile):
        super().__init__(profile)
        if profile.endpoint != "mock://local":
            raise ValueError("Invalid mock endpoint")

    def health(self) -> HealthStatus:
        return HealthStatus("ready")

    def text_chat(self, text: str) -> str:
        return f"演示后端[{self.id}]已收到：{text}"

    def clear_session(self):
        return {"ok": True}

    def endpoint_occupancy(self):
        return "occupied" if self.health().state == "ready" else "absent"
