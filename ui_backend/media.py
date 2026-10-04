"""Immutable, model-independent media boundary; local paths stay server-side."""
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

@dataclass(frozen=True)
class SelectionStamp:
    backend_id: str
    epoch: str
    revision: int

@dataclass(frozen=True)
class Attachment:
    id: str
    kind: Literal["image", "video"]
    media_type: str
    size_bytes: int
    local_path: Path

@dataclass(frozen=True)
class MediaRequest:
    text: str
    attachment: Attachment
