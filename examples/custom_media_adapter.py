"""Transport boundary example, not a claimed implementation of any vendor API.

Inject a trusted transport(kind, text, MIME, byte_chunks) which consumes the
iterator synchronously and returns text. Add service-specific authentication,
request/response schema, total timeout, limits and a real health probe yourself.
Never send attachment.local_path to another machine as its input!
"""
from ui_backend.adapter_base import BackendAdapter, HealthStatus
from ui_backend.adapters import BackendProtocolError
from ui_backend.media import MediaRequest

class CustomMediaAdapter(BackendAdapter):
    def __init__(self,profile,transport):
        super().__init__(profile); self.transport=transport

    def health(self):
        # Replace with a SHORT bounded remote probe in a real integration.
        return HealthStatus("ready")

    def text_chat(self,text):
        return self.transport("text",text,"text/plain",iter(()))

    def _media(self,payload: MediaRequest):
        attachment=payload.attachment
        with attachment.local_path.open("rb") as stream:
            chunks=iter(lambda:stream.read(65536),b"")
            answer=self.transport(attachment.kind,payload.text,attachment.media_type,chunks)
        if not isinstance(answer,str) or not answer.strip():
            raise BackendProtocolError("模型返回格式无效。")
        return answer

    def image(self,payload: MediaRequest) -> str:
        return self._media(payload)

    def video(self,payload: MediaRequest) -> str:
        return self._media(payload)
