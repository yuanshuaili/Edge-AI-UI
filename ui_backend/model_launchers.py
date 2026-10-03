"""Trusted server-side launchers, never supplied as commands by a browser."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaunchSpec:
    argv: tuple
    cwd: Path


from integrations.tinychat_nano import tinychat_qwen25


MODEL_LAUNCHERS = {"tinychat_qwen25": tinychat_qwen25}
