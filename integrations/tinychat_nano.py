"""Optional Nano launcher with fixed memory-safe Qwen quantization."""
import os
from pathlib import Path


def tinychat_qwen25(profile):
    from ui_backend.model_launchers import LaunchSpec
    if (profile.adapter != "tinychat" or profile.runtime_model_name != "Qwen2.5-3B-Instruct"
            or profile.endpoint != "tcp://127.0.0.1:8765"):
        raise ValueError("Nano launcher requires its matching adapter, model and loopback endpoint")
    root = Path(os.environ.get("EDGE_AI_TINYCHAT_ROOT", Path(__file__).resolve().parents[1])).resolve()
    python = root / "awq_env" / "bin" / "python"
    cwd = root / "llm-awq"
    model = root / "models" / "Qwen2.5-3B-Instruct"
    quant = cwd / "quant_cache" / "Qwen2.5-3B-Instruct-w4-g128-awq-v2.pt"
    if not python.is_file() or not model.is_dir() or not quant.is_file():
        raise ValueError("Nano integration installation is incomplete; configure EDGE_AI_TINYCHAT_ROOT")
    return LaunchSpec((str(python), "-u", "-m", "tinychat.llm_server", "--model_type", "qwen",
                       "--model_path", str(model), "--precision", "W4A16", "--load_quant", str(quant),
                       "--q_group_size", "128", "--backend-id", profile.id), cwd)
