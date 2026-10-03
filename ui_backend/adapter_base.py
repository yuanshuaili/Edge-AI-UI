"""Model-independent contract consumed by the Edge AI UI service."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .config import BackendConfig


class UnsupportedCapability(Exception):
    """An optional adapter operation is not implemented."""


@dataclass(frozen=True)
class HealthStatus:
    state: Literal["ready", "offline"]

    def __post_init__(self):
        if self.state not in ("ready", "offline"):
            raise ValueError("health state must be ready or offline")


class BackendAdapter(ABC):
    """One configured model endpoint; no frontend or ASR dependencies."""

    def __init__(self, profile: "BackendConfig"):
        self._profile = profile

    @property
    def id(self) -> str:
        return self._profile.id

    @property
    def metadata(self):
        return {
            "name": self._profile.name,
            "device": self._profile.device,
            "mode": self._profile.mode,
        }

    @property
    def capabilities(self):
        return MappingProxyType(dict(self._profile.capabilities))

    @property
    def available_inputs(self):
        return MappingProxyType(dict(self._profile.available_inputs))

    @abstractmethod
    def health(self) -> HealthStatus:
        """Return whether this configured endpoint can be contacted."""

    @abstractmethod
    def text_chat(self, text: str) -> str:
        """Return one complete text answer."""

    def stream_chat(self, text: str):
        raise UnsupportedCapability("stream_chat")

    def image(self, payload):
        raise UnsupportedCapability("image")

    def video(self, payload):
        raise UnsupportedCapability("video")

    def voice(self, payload):
        raise UnsupportedCapability("voice")

    def clear_session(self):
        raise UnsupportedCapability("clear_session")

    def endpoint_occupancy(self):
        """Managed lifecycle probe: occupied/absent/unknown, fail closed by default.

        Health offline is NOT evidence that an endpoint has been released.
        Required only when this adapter is used with a managed launcher.
        """
        return "unknown"
