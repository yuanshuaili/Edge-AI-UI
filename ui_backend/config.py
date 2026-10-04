"""Validated backend declarations for the exhibit UI."""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


CAPABILITIES = ("text", "voice", "image", "video", "tts")
INPUTS = ("text", "voice", "image", "video")
IMPLEMENTED_INPUTS = ("text", "voice", "image", "video")


class ConfigError(ValueError):
    """A backend declaration cannot be used safely."""


@dataclass(frozen=True)
class RuntimeConfig:
    managed: bool = False
    launcher: Optional[str] = None
    startup_timeout_seconds: float = 60
    shutdown_timeout_seconds: float = 10
    auto_start: bool = False


def _runtime(raw):
    from .model_launchers import MODEL_LAUNCHERS
    if not isinstance(raw, dict) or set(raw) - {"managed", "launcher", "startup_timeout_seconds", "shutdown_timeout_seconds", "auto_start"}:
        raise ConfigError("Unknown runtime configuration field")
    value = RuntimeConfig(**raw)
    if type(value.managed) is not bool or type(value.auto_start) is not bool:
        raise ConfigError("Runtime flags must be booleans")
    if value.managed:
        if not isinstance(value.launcher, str) or value.launcher not in MODEL_LAUNCHERS:
            raise ConfigError("Unknown managed model launcher")
    elif value.launcher is not None or value.auto_start:
        raise ConfigError("External backend cannot launch or auto start a model")
    for timeout in (value.startup_timeout_seconds, value.shutdown_timeout_seconds):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise ConfigError("Runtime timeout must be finite and between 0 and 600")
    return value


@dataclass(frozen=True)
class BackendConfig:
    id: str
    name: str
    device: str
    mode: str
    adapter: str
    endpoint: str
    capabilities: Dict[str, bool]
    available_inputs: Dict[str, bool]
    runtime_model_name: Optional[str]
    runtime: RuntimeConfig = RuntimeConfig()

    def public_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "device": self.device,
            "mode": self.mode,
            "capabilities": self.capabilities,
            "available_inputs": self.available_inputs,
            "managed": self.runtime.managed,
        }


@dataclass(frozen=True)
class AppConfig:
    project_name: str
    default_backend_id: str
    backends: Dict[str, BackendConfig]


def _boolean_map(raw, keys, label):
    if not isinstance(raw, dict) or set(raw) != set(keys):
        raise ConfigError(f"{label} must contain exactly: {', '.join(keys)}")
    if any(type(raw[key]) is not bool for key in keys):
        raise ConfigError(f"{label} values must be booleans")
    return {key: raw[key] for key in keys}


def load_config(path):
    from .adapter_registry import registered_adapter_kinds

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read backend config: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("backends"), list):
        raise ConfigError("backends must be a list")
    project_name = raw.get("project_name", "")
    default_id = raw.get("default_backend_id")
    if not isinstance(project_name, str):
        raise ConfigError("project_name must be a string when supplied")
    if not isinstance(default_id, str) or not default_id:
        raise ConfigError("default_backend_id is required")

    backends = {}
    for entry in raw["backends"]:
        if not isinstance(entry, dict):
            raise ConfigError("Each backend must be an object")
        fields = ("id", "name", "device", "mode", "adapter", "endpoint")
        if any(not isinstance(entry.get(key), str) or not entry[key].strip() for key in fields):
            raise ConfigError("Backend id, name, device, mode, adapter and endpoint are required")
        if entry["adapter"] not in registered_adapter_kinds():
            raise ConfigError(f"Unsupported adapter: {entry['adapter']}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", entry["id"]) or ".." in entry["id"]:
            raise ConfigError("Backend id must be a safe identifier")
        capabilities = _boolean_map(entry.get("capabilities"), CAPABILITIES, "capabilities")
        available_inputs = _boolean_map(entry.get("available_inputs"), INPUTS, "available_inputs")
        runtime_model_name = entry.get("runtime_model_name")
        if runtime_model_name is not None and (
            not isinstance(runtime_model_name, str) or not runtime_model_name.strip()
        ):
            raise ConfigError("runtime_model_name must be a non-empty string when supplied")
        if any(available_inputs[key] and not capabilities[key] for key in INPUTS):
            raise ConfigError(f"Enabled input exceeds capabilities: {entry['id']}")
        if available_inputs["voice"] and not available_inputs["text"]:
            raise ConfigError("Voice input requires a wired text chat input")
        if any(available_inputs[key] for key in INPUTS if key not in IMPLEMENTED_INPUTS):
            raise ConfigError("UI has no handler for the enabled input")
        backend = BackendConfig(
            **{key: entry[key] for key in fields},
            capabilities=capabilities,
            available_inputs=available_inputs,
            runtime_model_name=runtime_model_name,
            runtime=_runtime(entry.get("runtime", {})),
        )
        from .adapter_registry import ADAPTER_FACTORIES
        from .adapter_base import BackendAdapter
        for kind in ("image", "video"):
            if available_inputs[kind] and getattr(ADAPTER_FACTORIES[backend.adapter],kind,None) is getattr(BackendAdapter,kind):
                raise ConfigError(f"Enabled {kind} has no adapter implementation")
        if backend.id in backends:
            raise ConfigError(f"Duplicate backend id: {backend.id}")
        backends[backend.id] = backend

    if default_id not in backends:
        raise ConfigError(f"Unknown default backend: {default_id}")
    if any(item.runtime.auto_start and item.id != default_id for item in backends.values()):
        raise ConfigError("Only the default managed backend may auto start")
    return AppConfig(project_name.strip(), default_id, backends)
