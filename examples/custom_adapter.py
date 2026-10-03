"""Minimal example of a trusted, model-free custom adapter."""
from ui_backend.adapter_base import BackendAdapter, HealthStatus


class ExampleAdapter(BackendAdapter):
    def health(self):
        # Replace with a short, bounded probe of your own service.
        return HealthStatus("ready")

    def text_chat(self, text):
        # Replace this with a bounded HTTP/TCP/SDK request to your service.
        # Map private transport errors to safe BackendError subclasses.
        return f"示例后端[{self.id}]收到：{text}"

    def clear_session(self):
        # A real stateful service must actually reset its history here.
        return {"ok": True}
