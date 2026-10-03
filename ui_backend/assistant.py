"""Product identity shared by the exhibit UI and official model service."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


class AssistantConfigError(ValueError):
    """The official assistant profile is missing or invalid."""


@dataclass(frozen=True)
class AssistantProfile:
    id: str
    name: str
    subtitle: str
    welcome: str
    system_prompt: str

    def public_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "subtitle": self.subtitle,
            "welcome": self.welcome,
        }


def load_assistant(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AssistantConfigError(f"Cannot read assistant profile: {exc}") from exc
    fields = ("id", "name", "subtitle", "welcome", "system_prompt")
    if not isinstance(raw, dict) or any(
        not isinstance(raw.get(key), str) or not raw[key].strip() for key in fields
    ):
        raise AssistantConfigError("Assistant profile requires non-empty id, name, subtitle, welcome and system_prompt")
    return AssistantProfile(**{key: raw[key].strip() for key in fields})


def _safe_label(value, field):
    if (not isinstance(value, str) or not re.fullmatch(r"[\w .+·()\-]{1,96}", value)
            or ".." in value or not any(char.isalpha() for char in value)):
        raise AssistantConfigError(f"Unsafe runtime metadata: {field}")
    if field == "model name" and re.search(r"\.(?:pt|pth|bin|safetensors|gguf|onnx|engine)$", value, re.I):
        raise AssistantConfigError(f"Unsafe runtime metadata: {field}")
    return value


def build_runtime_prompt(profile, backend, precision):
    """Combine product identity with non-path facts for this running instance."""

    quantization = {"W4A16": "4-bit", "W16A16": "16-bit"}.get(precision)
    if quantization is None:
        raise AssistantConfigError("Unsupported precision for runtime metadata")
    name = _safe_label(profile.name, "assistant name")
    model = _safe_label(backend.runtime_model_name, "model name")
    device = _safe_label(backend.device, "device")
    return (
        profile.system_prompt
        + "\n\n以下是当前实例经过确认的运行信息。除非用户主动询问底层模型、推理框架、量化方式或部署技术，否则不要主动提及；如明确询问，必须如实回答：\n"
        + f"产品身份：{name}\n底层模型：{model}\n运行设备：{device}\n"
        + f"运行方式：本地边缘推理\n推理框架：TinyChat\n量化：{quantization}"
    )


def load_official_prompt(assistant_path, backend_config_path, backend_id, precision,
                         model_path=None, model_type="qwen"):
    """Fail closed before model loading if official identity cannot be applied."""

    from .config import ConfigError, load_config

    profile = load_assistant(assistant_path)
    if (model_type.lower() != "qwen" or not isinstance(model_path, str) or not model_path.strip()
            or "deepseek-r1-distill-qwen" in model_path.lower()):
        raise AssistantConfigError("Official identity currently requires a Qwen model path")
    try:
        config = load_config(backend_config_path)
    except ConfigError as exc:
        raise AssistantConfigError(f"Cannot load runtime backend metadata: {exc}") from exc
    selected_id = backend_id or config.default_backend_id
    backend = config.backends.get(selected_id)
    if backend is None or backend.adapter != "tinychat":
        raise AssistantConfigError(f"No TinyChat backend metadata for: {selected_id}")
    if backend.runtime_model_name is None:
        raise AssistantConfigError("Official backend requires runtime_model_name")
    model_name = _safe_label(backend.runtime_model_name, "model name")
    if Path(model_path).name != model_name:
        raise AssistantConfigError("Selected backend metadata does not match model directory name")
    return build_runtime_prompt(profile, backend, precision)
