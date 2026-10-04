"""No inference libraries: explicit media routing example for UI validation."""
from .adapters import MockAdapter
from .media import MediaRequest

class MockMediaAdapter(MockAdapter):
    def _reply(self, payload: MediaRequest):
        item=payload.attachment
        return (f"演示后端[{self.id}]已收到 {item.kind}（{item.size_bytes} bytes）："
                f"{payload.text}\n模拟，未分析媒体内容")

    def image(self, payload: MediaRequest) -> str:
        return self._reply(payload)

    def video(self, payload: MediaRequest) -> str:
        return self._reply(payload)
