"""Single-user ASR coordination, bounded events, and a local Unix bridge."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import stat
import threading
import uuid

from .adapters import BackendError
from .chat import BackendBusy, UnsupportedTextInput
from .voice_protocol import MAX_MESSAGE_BYTES, ProtocolError, decode_message, encode_message, validate_message


class ASRUnavailable(Exception):
    """The independent ASR process is not connected."""


class VoiceUnavailable(Exception):
    """The selected backend cannot accept a voice transcript now."""


class VoiceCoordinator:
    EVENT_LIMIT = 128

    def __init__(self, config, dispatcher, max_listen_seconds=20):
        if not 1 <= max_listen_seconds <= 120:
            raise ValueError("max_listen_seconds must be between 1 and 120")
        self.config = config
        self.dispatcher = dispatcher
        self.max_listen_seconds = max_listen_seconds
        self.selected_id = config.default_backend_id
        self.phase = "idle"
        self.active_id = None
        self.asr_connected = False
        self._sender = None
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._events = deque(maxlen=self.EVENT_LIMIT)
        self._next_event_id = 1
        self.epoch = uuid.uuid4().hex
        self._subscribers = 0
        self.model_state = None
        self._resetting = False
        self._media_active = False
        self.conversation_revision = 0
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-chat")
        self._answer_future = None
        self._closed = False
        dispatcher.set_busy_listener(self._on_backend_busy)

    def _on_backend_busy(self, backend_id, busy):
        with self._lock:
            if backend_id == self.selected_id:
                self._record("backend_busy", backend_id=backend_id, busy=busy)

    def attach_sender(self, sender):
        with self._lock:
            self._sender = sender

    def subscriber_enter(self):
        with self._lock:
            self._subscribers += 1

    def subscriber_exit(self):
        with self._lock:
            if self._subscribers == 0:
                return
            self._subscribers -= 1
            if self._subscribers == 0 and self.active_id is not None:
                self.cancel_listening()

    def _record(self, event_type, **values):
        event = {"id": self._next_event_id, "type": event_type,
                 "epoch": self.epoch, **values}
        self._next_event_id += 1
        self._events.append(event)
        self._changed.notify_all()
        return event

    def snapshot(self):
        with self._lock:
            backend_id = self.selected_id
            return {
                "selected_backend_id": backend_id,
                "phase": self.phase,
                "request_id": self.active_id,
                "asr_connected": self.asr_connected,
                "backend_busy": self._media_active or self.dispatcher.is_busy(backend_id),
                "conversation_revision": self.conversation_revision,
                "event_id": self._next_event_id - 1,
                "epoch": self.epoch,
                "model_state": self.model_state,
            }

    def events_after(self, last_id):
        with self._lock:
            if (last_id is None or last_id >= self._next_event_id
                    or (self._events and last_id < self._events[0]["id"] - 1)):
                return [{"id": self._next_event_id - 1, "type": "state", **self.snapshot()}]
            return [dict(event) for event in self._events if event["id"] > last_id]

    def wait_for_events(self, last_id, timeout=5):
        with self._changed:
            events = self.events_after(last_id)
            if not events:
                self._changed.wait(timeout=timeout)
                events = self.events_after(last_id)
            return events

    def set_asr_connected(self, connected):
        with self._lock:
            if self.asr_connected == connected:
                return
            self.asr_connected = connected
            if not connected:
                self.active_id = None
                if self.phase in ("listening", "recognizing"):
                    self.phase = "idle"
            self._record("asr_status", asr_connected=connected, phase=self.phase)

    def select_backend(self, backend_id):
        with self._lock:
            if backend_id not in self.config.backends:
                raise KeyError(backend_id)
            self.selected_id = backend_id
            profile = self.config.backends[backend_id]
            if self.active_id and not profile.available_inputs["voice"]:
                self.cancel_listening()
            self._record("selection", backend_id=backend_id,
                         backend_busy=self.dispatcher.is_busy(backend_id))

    def model_changed(self, snapshot):
        with self._lock:
            self.model_state = snapshot
            self._record("model_state", model_state=snapshot)

    def request_model_action(self, action):
        with self._lock:
            if self.phase == "thinking" or self._resetting or self._media_active:
                raise BackendBusy("模型正在回答，请稍候。")
            self.cancel_listening()  # Invalidate request before any late ASR transcript.
            return action()

    def clear_session(self, backend_id):
        with self._lock:
            if self._resetting or self._media_active or self.active_id is not None or self.phase in ("listening", "recognizing", "thinking"):
                raise BackendBusy("请等待当前交互结束后再开始新对话。")
            self._resetting = True
        try:
            result = self.dispatcher.clear_session(backend_id)
            with self._lock:
                self.conversation_revision += 1
            return result
        finally:
            with self._lock:
                self._resetting = False

    def can_start(self):
        with self._lock:
            profile = self.config.backends[self.selected_id]
            return (not self._closed and self.asr_connected and profile.capabilities["voice"]
                    and profile.available_inputs["voice"] and profile.available_inputs["text"]
                    and self.phase != "thinking" and self.active_id is None and not self._resetting and not self._media_active
                    and self.dispatcher.can_operate(self.selected_id)
                    and not self.dispatcher.is_busy(self.selected_id))

    def start_listening(self):
        with self._lock:
            if not self.asr_connected or self._sender is None:
                raise ASRUnavailable("语音服务未连接。")
            if not self.can_start():
                raise VoiceUnavailable("当前无法开始语音输入。")
            request_id = uuid.uuid4().hex
            command = {"v": 1, "type": "start_listening", "request_id": request_id,
                       "max_listen_seconds": self.max_listen_seconds}
            try:
                self._sender(command)
            except OSError as exc:
                self.set_asr_connected(False)
                raise ASRUnavailable("语音服务未连接。") from exc
            self.active_id = request_id
            self.phase = "listening"
            self._record("listening", request_id=request_id)
            return request_id

    def cancel_listening(self):
        with self._lock:
            if self.active_id is None:
                return
            request_id = self.active_id
            self.active_id = None
            self.phase = "idle"
            self._record("idle", request_id=request_id)
            try:
                if self._sender:
                    self._sender({"v": 1, "type": "stop_listening", "request_id": request_id})
            except OSError:
                self.set_asr_connected(False)

    def handle_asr_event(self, message):
        validate_message(message)
        kind = message["type"]
        if kind not in ("status", "idle", "listening", "speech_detected", "recognizing", "transcript", "error"):
            raise ProtocolError("ASR sent a control command")
        with self._lock:
            if self._closed:
                return
            if kind == "status":
                self._record("asr_status", asr_connected=self.asr_connected, phase=self.phase)
                return
            request_id = message["request_id"]
            if request_id != self.active_id:
                return
            if kind in ("listening", "speech_detected"):
                self.phase = "listening"
                self._record(kind, request_id=request_id)
            elif kind == "recognizing":
                self.phase = "recognizing"
                self._record("recognizing", request_id=request_id)
            elif kind == "idle":
                self.active_id = None
                self.phase = "idle"
                self._record("idle", request_id=request_id)
            elif kind == "error":
                self.active_id = None
                self.phase = "idle"
                hint = "未检测到有效语音，请重试" if message.get("code") == "no_speech" else "语音识别暂不可用，请重试。"
                self._record("error", request_id=request_id, message=hint)
            elif kind == "transcript":
                backend_id = self.selected_id
                profile = self.config.backends[backend_id]
                self.active_id = None
                if not profile.available_inputs["voice"] or not profile.available_inputs["text"]:
                    self.phase = "idle"
                    self._record("error", request_id=request_id, message="当前模型不支持语音输入。")
                    return
                text = message["text"].strip()
                self._record("transcript_ready", request_id=request_id,
                             backend_id=backend_id, text=text)
                self.phase = "thinking"
                self._record("thinking", request_id=request_id, backend_id=backend_id)
                self._answer_future = self._worker.submit(self._answer, request_id, backend_id, text)

    def _answer(self, request_id, backend_id, text):
        try:
            result = self.dispatcher.dispatch_text_chat(backend_id, text)
        except (BackendBusy, UnsupportedTextInput, BackendError):
            with self._lock:
                self.phase = "idle"
                self._record("error", request_id=request_id, backend_id=backend_id,
                             message="当前无法完成回答，请稍后重试。")
        except Exception:
            with self._lock:
                self.phase = "idle"
                self._record("error", request_id=request_id, backend_id=backend_id,
                             message="当前无法完成回答，请稍后重试。")
        else:
            with self._lock:
                self.phase = "answered"
                self._record("answered", request_id=request_id, backend_id=backend_id,
                             text=result.text, latency_ms=result.latency_ms)

    def dispatch_media_chat(self, backend_id, text, identifier, store, selection_provider):
        # Share atomic admission with capture/reset/switch, not the model call.
        # Never hold the coordinator lock while an Adapter performs inference.
        with self._lock:
            if (self._resetting or self._media_active or self.active_id is not None
                    or self.phase in ("listening", "recognizing", "thinking")):
                raise BackendBusy("请等待当前交互结束后再发送附件。")
            self._media_active = True
        try:
            stamp = selection_provider()
            with store.consume(identifier, stamp) as attachment:
                return self.dispatcher.dispatch_media_chat(backend_id, text, attachment, stamp)
        finally:
            with self._lock:
                self._media_active = False

    @property
    def has_pending_answer(self):
        with self._lock:
            return self._answer_future is not None and not self._answer_future.done()

    def close(self):
        with self._changed:
            self._closed = True
            self.cancel_listening()
            self._changed.notify_all()  # Wake SSE handlers to observe server shutdown.
        self._worker.shutdown(wait=False, cancel_futures=True)


class UnixAsrBridge:
    """One same-user ASR connection; browser never sees this socket."""

    def __init__(self, path, coordinator):
        self.path = Path(path)
        self.coordinator = coordinator
        self._listener = None
        self._client = None
        self._client_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._stop = threading.Event()
        self._accept_thread = None
        self._inode = None

    def start(self):
        if os.path.lexists(self.path):
            item = os.lstat(self.path)
            if not stat.S_ISSOCK(item.st_mode) or item.st_uid != os.getuid():
                raise RuntimeError("ASR socket path is not an owned socket")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.2)
                probe.connect(str(self.path))
            except ConnectionRefusedError:
                self.path.unlink()
            else:
                raise RuntimeError("An ASR socket service is already active")
            finally:
                probe.close()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous_umask = os.umask(0o177)
        try:
            listener.bind(str(self.path))
        finally:
            os.umask(previous_umask)
        os.chmod(self.path, 0o600)
        self._inode = os.lstat(self.path).st_ino
        listener.listen(2)
        listener.settimeout(0.2)
        self._listener = listener
        self.coordinator.attach_sender(self.send)
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def send(self, message):
        payload = encode_message(message)
        with self._client_lock:
            if self._client is None:
                raise ASRUnavailable("语音服务未连接。")
            self._client.sendall(payload)

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._status_lock:
                with self._client_lock:
                    if self._client is not None:
                        client.close()
                        continue
                    self._client = client
                client.settimeout(0.2)
                self.coordinator.set_asr_connected(True)
            threading.Thread(target=self._read_loop, args=(client,), daemon=True).start()

    def _read_loop(self, client):
        buffer = bytearray()
        try:
            while not self._stop.is_set():
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > MAX_MESSAGE_BYTES and b"\n" not in buffer:
                    break
                while b"\n" in buffer:
                    frame, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    try:
                        self.coordinator.handle_asr_event(decode_message(bytes(frame) + b"\n"))
                    except ProtocolError:
                        return
        finally:
            with self._status_lock:
                with self._client_lock:
                    was_current = self._client is client
                    if was_current:
                        self._client = None
                if was_current:
                    self.coordinator.set_asr_connected(False)
            client.close()

    def close(self):
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1)
        if self._inode is not None and os.path.lexists(self.path):
            item = os.lstat(self.path)
            if stat.S_ISSOCK(item.st_mode) and item.st_ino == self._inode:
                self.path.unlink()
        self.coordinator.set_asr_connected(False)
