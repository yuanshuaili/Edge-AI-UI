"""The one text dispatch path used by HTTP chat and ASR transcripts."""

import threading
import time
from dataclasses import dataclass
from contextlib import contextmanager


class BackendBusy(Exception):
    """This backend already has an in-flight request from this UI."""


class UnsupportedTextInput(Exception):
    """The configured backend does not accept text."""


class OperationGate:
    """Atomic admission: generation/reset cannot overlap a lifecycle transition."""
    def __init__(self):
        self._lock = threading.Lock()
        self._active = 0
        self._transition = False
        self._closed = False

    @property
    def blocked(self):
        with self._lock:
            return self._transition or self._closed

    def enter_operation(self):
        with self._lock:
            if self._transition or self._closed:
                raise BackendBusy("模型状态正在变化，请稍候。")
            self._active += 1

    def leave_operation(self):
        with self._lock:
            self._active -= 1

    def begin_transition(self):
        with self._lock:
            if self._active or self._transition or self._closed:
                raise BackendBusy("当前交互尚未结束，请稍候。")
            self._transition = True

    def end_transition(self):
        with self._lock:
            self._transition = False

    def close(self):
        with self._lock:
            self._closed = True


@dataclass(frozen=True)
class ChatResult:
    backend_id: str
    text: str
    latency_ms: int


class ChatDispatcher:
    def __init__(self, config, adapters):
        self.config = config
        self.adapters = adapters
        self._locks = {backend_id: threading.Lock() for backend_id in config.backends}
        self._busy_listener = None
        self.gate = OperationGate()
        self._admission = None
        self._selection_provider = None

    def set_selection_provider(self, provider):
        self._selection_provider = provider

    def set_admission(self, admission):
        self._admission = admission

    def can_operate(self, backend_id):
        return not self.gate.blocked and (self._admission is None or self._admission(backend_id))

    @contextmanager
    def _operation(self, backend_id):
        self.gate.enter_operation()
        try:
            if self._admission and not self._admission(backend_id):
                from .adapters import BackendUnavailable
                raise BackendUnavailable("模型尚未连接，请先启动模型。")
            yield
        finally:
            self.gate.leave_operation()

    def set_busy_listener(self, listener):
        self._busy_listener = listener

    def is_busy(self, backend_id: str) -> bool:
        return self._locks[backend_id].locked()

    def dispatch_text_chat(self, backend_id: str, text: str) -> ChatResult:
        profile = self.config.backends[backend_id]
        if not profile.available_inputs["text"]:
            raise UnsupportedTextInput("当前模型未开放文本输入。")
        with self._operation(backend_id):
            return self._chat(backend_id, text)

    def _chat(self, backend_id, text):
        return self._locked_call(backend_id, lambda: self.adapters[backend_id].text_chat(text))

    def dispatch_media_chat(self, backend_id, text, attachment, stamp):
        from .attachments import AttachmentError
        from .adapter_base import UnsupportedCapability
        from .adapters import BackendProtocolError, BackendUnavailable
        from .media import MediaRequest
        profile = self.config.backends[backend_id]
        kind=attachment.kind
        if kind not in ("image","video") or not profile.capabilities[kind] or not profile.available_inputs[kind]:
            raise UnsupportedCapability(kind)
        if not isinstance(text,str) or len(text)>4000:
            raise AttachmentError("invalid_request", "请输入不超过 4000 字的文字。")
        with self._operation(backend_id):
            if self._selection_provider is None or self._selection_provider()!=stamp or stamp.backend_id!=backend_id:
                raise AttachmentError("stale_attachment", "模型或对话已变化，请重新选择附件。", 409)
            if self.adapters[backend_id].health().state!="ready":
                raise BackendUnavailable("模型尚未连接，请稍后重试。")
            def call():
                answer=getattr(self.adapters[backend_id],kind)(MediaRequest(text,attachment))
                if not isinstance(answer,str) or not answer.strip() or len(answer)>64000:
                    raise BackendProtocolError("模型返回格式无效，请稍后重试。")
                return answer
            return self._locked_call(backend_id,call)

    def _locked_call(self, backend_id, call):
        lock = self._locks[backend_id]
        if not lock.acquire(blocking=False):
            raise BackendBusy("模型正在回答上一条消息，请稍候。")
        try:
            if self._busy_listener is not None:
                self._busy_listener(backend_id, True)
            started = time.monotonic()
            answer = call()
            return ChatResult(backend_id, answer, round((time.monotonic() - started) * 1000))
        finally:
            lock.release()
            if self._busy_listener is not None:
                self._busy_listener(backend_id, False)

    def clear_session(self, backend_id):
        with self._operation(backend_id):
            return self._clear(backend_id)

    def _clear(self, backend_id):
        lock = self._locks[backend_id]
        if not lock.acquire(blocking=False):
            raise BackendBusy("模型正在回答，请稍候再开始新对话。")
        try:
            if self._busy_listener:
                self._busy_listener(backend_id, True)
            result = self.adapters[backend_id].clear_session()
            if not isinstance(result, dict) or result.get("ok") is not True:
                from .adapters import BackendProtocolError
                raise BackendProtocolError("暂时无法开始新对话，请稍后重试。")
            return result
        finally:
            lock.release()
            if self._busy_listener:
                self._busy_listener(backend_id, False)
