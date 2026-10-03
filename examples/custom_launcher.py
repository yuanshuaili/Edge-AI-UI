"""Server-only launcher template: arguments are code, not browser input."""
import os
from pathlib import Path
from ui_backend.model_launchers import LaunchSpec


def example_launcher(profile):
    root = Path(os.environ["EXAMPLE_MODEL_ROOT"]).resolve()
    # These paths and flags are an integration example; use your trusted service.
    return LaunchSpec((str(root / "venv/bin/python"), "-m", "example_model.server"), root)
