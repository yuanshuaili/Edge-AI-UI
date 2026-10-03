"""Conservative staged/tracked-file audit; no dependencies, no file mutations."""
import json
from pathlib import Path
import re
import subprocess
import sys


def main():
    root = Path.cwd()
    names = subprocess.check_output(["git", "ls-files", "-z"], text=True).split("\0")
    files = [Path(name) for name in names if name]
    forbidden_dirs = {"models", "awq_env", "asr_env", "venv", ".venv", "quant_cache", "awq_cache",
                      "flash-attention", "llm-awq", "__pycache__", ".ssh", ".codex", ".aws", "logs"}
    forbidden_suffixes = {".pt", ".pth", ".bin", ".safetensors", ".onnx", ".gguf", ".pyc", ".sock", ".log", ".pem", ".key"}
    secrets = [re.compile(pattern.encode()) for pattern in (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
        r"AKIA[0-9A-Z]{16}", r"sk-(?:proj-)?[A-Za-z0-9_-]{24,}",
    )]
    problems, sizes = [], []
    for relative in files:
        path = root / relative
        if path.is_symlink():
            problems.append(f"symlink: {relative}")
            continue
        data = path.read_bytes()
        sizes.append((len(data), str(relative)))
        if forbidden_dirs.intersection(relative.parts) or relative.suffix.lower() in forbidden_suffixes:
            problems.append(f"forbidden file: {relative}")
        if relative.as_posix() in ("config/assistant.json", "config/backends.json") or relative.name.startswith(".env"):
            problems.append(f"local config: {relative}")
        if len(data) > 1_048_576 or b"\0" in data:
            problems.append(f"large/binary file: {relative}")
        if any(pattern.search(data) for pattern in secrets):
            problems.append(f"secret-like content: {relative}")
        if relative.parts[0] in ("ui", "ui_backend") and re.search(rb"/(?:home|Users)/|tinychat_reproduction|192\.168\.55\.1|127\.0\.0\.1:8765", data):
            problems.append(f"site-specific core dependency: {relative}")
    print(json.dumps({"tracked_files": len(files), "tracked_bytes": sum(size for size, _ in sizes),
                      "largest": sorted(sizes, reverse=True)[:20], "problems": problems}, indent=2))
    return bool(problems)


if __name__ == "__main__":
    sys.exit(main())
