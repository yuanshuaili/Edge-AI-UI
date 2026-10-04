"""Small same-origin HTTP facade for model adapters and static exhibit assets."""

import json
import threading
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .adapters import (
    BackendProtocolError,
    BackendRejected,
    BackendTimeout,
    BackendUnavailable,
)
from .adapter_registry import build_adapter
from .adapter_base import UnsupportedCapability
from .chat import BackendBusy, ChatDispatcher, UnsupportedTextInput
from .config import load_config
from .assistant import load_assistant
from .model_manager import LifecycleError, ModelManager
from .voice import ASRUnavailable, UnixAsrBridge, VoiceCoordinator, VoiceUnavailable
from .attachments import AttachmentError, AttachmentLimits, AttachmentStore
from .attachment_http import AttachmentHttp
from .media import SelectionStamp


MAX_REQUEST_BYTES = 16_384
MAX_TEXT_CHARS = 4_000
DEFAULT_ASSISTANT_PATH = Path(__file__).resolve().parents[1] / "config" / "assistant.json"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/media.js": ("media.js", "text/javascript; charset=utf-8"),
}


def create_server(
    config_path, host="127.0.0.1", port=8080, allowed_hosts=(),
    client_timeout=10.0, max_clients=16, assistant_path=None,
    asr_socket_path=None, max_listen_seconds=20,
    model_log_dir=None,
    attachment_limits=None, attachment_parent_dir=None,
):
    """Construct, but do not start, the UI HTTP service."""

    if client_timeout <= 0 or max_clients < 1:
        raise ValueError("client_timeout and max_clients must be positive")
    config = load_config(config_path)
    assistant = load_assistant(assistant_path or DEFAULT_ASSISTANT_PATH)
    adapters = {
        backend_id: build_adapter(backend)
        for backend_id, backend in config.backends.items()
    }
    dispatcher = ChatDispatcher(config, adapters)
    voice = VoiceCoordinator(config, dispatcher, max_listen_seconds=max_listen_seconds)
    manager = ModelManager(config, adapters, dispatcher,
                           model_log_dir or Path(__file__).resolve().parents[1] / "logs")
    store = AttachmentStore(attachment_limits or AttachmentLimits(),attachment_parent_dir)
    def selection_stamp():
        snapshot=manager.refresh()
        return SelectionStamp(snapshot["selected_backend_id"],snapshot["epoch"],snapshot["revision"],
                              voice.snapshot()["conversation_revision"])
    dispatcher.set_selection_provider(selection_stamp)
    def model_changed(snapshot):
        if snapshot["state"] in ("starting","switching","stopping"):
            store.invalidate_idle()
        voice.model_changed(snapshot)
    manager.set_listener(model_changed, voice.select_backend)
    manager.epoch = voice.epoch
    static_dir = Path(__file__).resolve().parents[1] / "ui"
    extra_hosts = {name.lower() for name in allowed_hosts}
    slots = threading.BoundedSemaphore(max_clients)
    active_condition=threading.Condition()
    active_count=0
    closing=threading.Event()
    def can_upload(backend_id,kind):
        profile=config.backends.get(backend_id)
        return bool(not closing.is_set() and profile and kind in ("image","video")
            and profile.capabilities[kind] and profile.available_inputs[kind]
            and backend_id==manager.snapshot()["selected_backend_id"]
            and dispatcher.can_operate(backend_id) and not dispatcher.is_busy(backend_id)
            and voice.snapshot()["phase"] not in ("listening","recognizing","thinking")
            and adapters[backend_id].health().state=="ready")
    attachments=AttachmentHttp(store,dispatcher,selection_stamp,can_upload)

    class Handler(BaseHTTPRequestHandler):
        server_version = "EdgeAIUI/1.0"
        protocol_version = "HTTP/1.1"  # Required for the stdlib Expect preflight hook.

        def _json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status, code, message):
            self._json(status, {"error": {"code": code, "message": message}})

        def _backend(self, backend_id):
            backend = config.backends.get(backend_id)
            if backend is None:
                self._error(404, "unknown_backend", "所选模型未配置。")
            return backend

        def _valid_host(self):
            raw = self.headers.get("Host", "")
            if not raw or any(char in raw for char in "/\\,@"):
                return False
            try:
                parsed = urlsplit("http://" + raw)
                hostname = parsed.hostname
                request_port = parsed.port
            except ValueError:
                return False
            if not hostname or parsed.path or parsed.query or parsed.fragment:
                return False
            if request_port != self.server.server_port:
                return False
            local_address = self.connection.getsockname()[0].lower()
            valid = {local_address} | extra_hosts
            if local_address in ("127.0.0.1", "::1"):
                valid.update(("localhost", "127.0.0.1", "::1"))
            return hostname.lower() in valid

        def _require_valid_host(self):
            if self._valid_host():
                return True
            self.close_connection = True
            self._error(421, "invalid_host", "访问地址与当前服务不匹配。")
            return False

        def _require_origin(self):
            origins=self.headers.get_all("Origin",[])
            if not origins: return True  # CLI clients; Host is not authentication.
            if len(origins)==1 and origins[0]=="http://"+self.headers.get("Host",""):
                return True
            self.close_connection=True
            self._error(403,"invalid_origin","访问来源与当前页面不匹配。")
            return False

        def handle_expect_100(self):
            if not self._require_valid_host() or not self._require_origin(): return False
            parsed=urlsplit(self.path)
            if self.command=="POST" and parsed.path=="/api/attachments":
                return attachments.expect(self,parse_qs(parsed.query,keep_blank_values=True))
            self.close_connection=True
            self._error(417,"unsupported_expect","当前请求不支持预发送。")
            return False

        def do_DELETE(self):
            self.close_connection=True
            if not self._require_valid_host() or not self._require_origin(): return
            parsed=urlsplit(self.path)
            if parsed.path.startswith("/api/attachments/") and not parsed.query:
                attachments.delete(self,parsed.path[len("/api/attachments/"):])
            else: self._error(400,"invalid_request","附件删除请求格式无效。")

        def do_GET(self):
            if not self._require_valid_host():
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/api/assistant":
                self._json(200, assistant.public_dict())
                return
            if parsed.path == "/api/backends":
                self._json(200, {
                    "project_name": assistant.name,
                    "default_backend_id": config.default_backend_id,
                    "backends": [item.public_dict() for item in config.backends.values()],
                    "api_features": {
                        "sessions": False,
                        "clear_conversation": True,
                        "streaming": False,
                        "uploads": True,
                        "model_lifecycle": True,
                    },
                    "attachment_limits": store.limits.public_dict(),
                })
                return
            if parsed.path == "/api/model/state":
                self._json(200, manager.refresh())
                return
            if parsed.path == "/api/status":
                values = parse_qs(parsed.query)
                backend_id = values.get("backend_id", [config.default_backend_id])[0]
                backend = self._backend(backend_id)
                if backend is None:
                    return
                self._json(200, {
                    "backend_id": backend_id,
                    "transport": "reachable" if adapters[backend_id].health().state == "ready" else "offline",
                    "capabilities": backend.capabilities,
                    "available_inputs": backend.available_inputs,
                })
                return
            if parsed.path == "/api/voice/state":
                self._json(200, {**voice.snapshot(), "can_listen": voice.can_start()})
                return
            if parsed.path == "/api/voice/events":
                self._voice_events()
                return
            item = STATIC_FILES.get(parsed.path)
            if item is None:
                self._error(404, "not_found", "页面不存在。")
                return
            filename, content_type = item
            try:
                body = (static_dir / filename).read_bytes()
            except OSError:
                self._error(500, "asset_missing", "页面资源暂不可用。")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _voice_events(self):
            self.close_connection = True
            raw_id = self.headers.get("Last-Event-ID")
            if raw_id is None:
                raw_id = parse_qs(urlsplit(self.path).query).get("last_id", [None])[0]
            try:
                last_id = None if raw_id is None else max(0, int(raw_id))
            except ValueError:
                self._error(400, "invalid_event_id", "事件编号无效。")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            voice.subscriber_enter()
            try:
                snapshot = voice.snapshot()
                intro_id = snapshot["event_id"] if last_id is None else last_id
                intro = {"id": intro_id, "type": "state", **snapshot}
                data = json.dumps(intro, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(f"id: {intro_id}\nevent: voice\ndata: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                if last_id is None:
                    last_id = snapshot["event_id"]
                while not closing.is_set():
                    events = voice.wait_for_events(last_id, timeout=5)
                    if closing.is_set(): break
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        for event in events:
                            data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                            self.wfile.write(f"id: {event['id']}\nevent: voice\ndata: {data}\n\n".encode("utf-8"))
                            last_id = event["id"]
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                voice.subscriber_exit()

        def do_POST(self):
            # Requests rejected before their body is consumed must never reuse
            # the connection. Keep HTTP/1.1 only for the validated Expect hook.
            self.close_connection = True
            if not self._require_valid_host():
                return
            if not self._require_origin(): return
            parsed=urlsplit(self.path)
            path = parsed.path
            if path=="/api/attachments":
                attachments.upload(self,parse_qs(parsed.query,keep_blank_values=True))
                return
            if path not in ("/api/chat", "/api/session/clear", "/api/voice/control", "/api/voice/selection",
                            "/api/model/activate", "/api/model/stop"):
                self._error(404, "not_found", "接口不存在。")
                return
            if self.headers.get_content_type() != "application/json":
                self._error(415, "unsupported_media_type", "请使用 JSON 格式提交。")
                return
            try:
                if len(self.headers.get_all("Content-Length",[]))!=1 or self.headers.get_all("Transfer-Encoding"):
                    raise ValueError("ambiguous request length")
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_REQUEST_BYTES:
                self._error(413, "invalid_request", "请求内容过大或缺少长度。")
                return
            try:
                request = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "invalid_request", "请求不是有效的 JSON。")
                return
            if not isinstance(request, dict):
                self._error(400, "invalid_request", "请求格式无效。")
                return
            if path in ("/api/model/activate", "/api/model/stop", "/api/voice/selection"):
                if path == "/api/model/stop":
                    if request:
                        self._error(400, "invalid_request", "停止请求格式无效。")
                        return
                    action = manager.stop
                else:
                    if set(request) != {"backend_id"}:
                        self._error(400, "invalid_request", "模型选择请求格式无效。")
                        return
                    backend_id = request.get("backend_id")
                    if not isinstance(backend_id, str) or backend_id not in config.backends:
                        self._error(404, "unknown_backend", "所选模型未配置。")
                        return
                    action = lambda: manager.activate(backend_id)
                try:
                    snapshot = voice.request_model_action(action)
                except BackendBusy as exc:
                    self._error(409, "backend_busy", str(exc))
                except LifecycleError as exc:
                    self._error(400, exc.code, str(exc))
                else:
                    if path == "/api/voice/selection":
                        # Backward-compatible alias; bounded wait, never queue a second model.
                        try:
                            manager.wait(1)
                        except TimeoutError:
                            pass
                        self._json(200, {**voice.snapshot(), "can_listen": voice.can_start()})
                    else:
                        self._json(202, snapshot)
                return
            if path == "/api/voice/control":
                action = request.get("action")
                try:
                    if action == "start":
                        request_id = voice.start_listening()
                        self._json(200, {"request_id": request_id, **voice.snapshot()})
                    elif action == "stop":
                        voice.cancel_listening()
                        self._json(200, voice.snapshot())
                    else:
                        self._error(400, "invalid_action", "无效的语音操作。")
                except ASRUnavailable as exc:
                    self._error(503, "asr_unavailable", str(exc))
                except VoiceUnavailable as exc:
                    self._error(409, "voice_unavailable", str(exc))
                return
            backend_id = request.get("backend_id", config.default_backend_id)
            if not isinstance(backend_id, str):
                self._error(400, "invalid_request", "模型编号无效。")
                return
            backend = self._backend(backend_id)
            if backend is None:
                return
            if request.get("session_id") is not None:
                self._error(400, "unsupported_session", "当前版本只支持单用户共享会话。")
                return
            if path == "/api/session/clear":
                if set(request) != {"backend_id"}:
                    self._error(400, "invalid_request", "新对话请求格式无效。")
                    return
                try:
                    voice.clear_session(backend_id)
                except BackendBusy as exc:
                    self._error(409, "backend_busy", str(exc))
                except UnsupportedCapability:
                    self._error(400, "unsupported_clear", "当前模型暂不支持新对话。")
                except BackendUnavailable:
                    self._error(503, "backend_unavailable", "模型服务暂不可用，未清空对话。")
                except BackendTimeout:
                    self._error(504, "backend_timeout", "新对话请求超时，当前页面记录已保留。")
                except (BackendProtocolError, BackendRejected):
                    self._error(502, "backend_error", "暂时无法开始新对话，当前记录已保留。")
                else:
                    store.invalidate_idle()
                    self._json(200, {"ok": True, "backend_id": backend_id,
                                     "event_id": voice.snapshot()["event_id"], "epoch": voice.epoch})
                return
            text = request.get("text")
            if set(request)-{"backend_id","text","attachment_id","session_id"}:
                self._error(400,"invalid_request","请求包含不支持的字段。")
                return
            attachment_id=request.get("attachment_id")
            if not isinstance(text, str) or (not text.strip() and attachment_id is None) or len(text) > MAX_TEXT_CHARS:
                self._error(400, "invalid_request", "请输入不超过 4000 字的文字。")
                return
            try:
                if attachment_id is None:
                    result = dispatcher.dispatch_text_chat(backend_id, text.strip())
                else:
                    result=voice.dispatch_media_chat(backend_id,text.strip(),attachment_id,store,selection_stamp)
            except AttachmentError as exc:
                self._error(exc.http_status,exc.code,str(exc))
            except UnsupportedCapability:
                self._error(400,"unsupported_input","当前模型不支持此类附件。")
            except UnsupportedTextInput as exc:
                self._error(400, "unsupported_input", str(exc))
            except BackendBusy as exc:
                self._error(409, "backend_busy", str(exc))
            except BackendUnavailable as exc:
                self._error(503, "backend_unavailable", str(exc))
            except BackendTimeout as exc:
                self._error(504, "backend_timeout", str(exc))
            except BackendRejected:
                self._error(502, "backend_error", "当前无法完成请求，请稍后重试。")
            except BackendProtocolError as exc:
                self._error(502, "backend_error", str(exc))
            except Exception:
                logging.getLogger(__name__).exception("Adapter request failed")
                self._error(502,"backend_error","当前无法完成请求，请稍后重试。")
            else:
                self._json(200, {
                    "backend_id": result.backend_id,
                    "session_id": None,
                    "text": result.text,
                    "latency_ms": result.latency_ms,
                })

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def server_close(self):
            closing.set()
            store.close()  # Cancel partials, but retain in-flight model leases.
            dispatcher.gate.close()
            if self.voice_bridge is not None:
                self.voice_bridge.close()
                self.voice_bridge = None
            voice.close()
            deadline=time.monotonic()+130
            with active_condition:
                while active_count and time.monotonic()<deadline:
                    active_condition.wait(min(.2,max(0,deadline-time.monotonic())))
            if active_count or store.has_leases:
                logging.getLogger(__name__).warning("Shutdown drain timed out; active resources retained for safe maintenance")
            else:
                manager.close()
            super().server_close()

        def get_request(self):
            request, client_address = super().get_request()
            request.settimeout(client_timeout)
            return request, client_address

        def process_request(self, request, client_address):
            nonlocal active_count
            if closing.is_set():
                self.shutdown_request(request); return
            if not slots.acquire(blocking=False):
                try:
                    request.settimeout(0.5)
                    request.sendall(
                        b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                except OSError:
                    pass
                self.shutdown_request(request)
                return
            try:
                with active_condition: active_count+=1
                super().process_request(request, client_address)
            except BaseException:
                with active_condition: active_count-=1; active_condition.notify_all()
                slots.release()
                raise

        def process_request_thread(self, request, client_address):
            nonlocal active_count
            try:
                super().process_request_thread(request, client_address)
            finally:
                slots.release()
                with active_condition: active_count-=1; active_condition.notify_all()

    server = Server((host, port), Handler)
    server.voice = voice
    server.client_timeout=client_timeout
    server.attachment_store=store
    server.model_manager = manager
    server.voice_bridge = None
    if asr_socket_path is not None:
        try:
            bridge = UnixAsrBridge(asr_socket_path, voice)
            bridge.start()
            server.voice_bridge = bridge
        except Exception:
            server.server_close()
            raise
    if config.backends[config.default_backend_id].runtime.auto_start:
        manager.activate(config.default_backend_id)
    return server
