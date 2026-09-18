"""Pinned supplied processor validation, not a substitute for new consumer tests."""
import importlib.util
from pathlib import Path
import sys

from .codec import digest

PROCESSOR_SHA256 = "a2cd37cbf447f591bf11f7f397f0fcb817ba1032f6ca5c9ca0efb967f50c7a4b"
RENDERER_SHA256 = "ae3fc8a38cdc1df7c7ddff3b9f3f834401763d09d5b9891e56022b786f3f6c0c"
SCRIPTS = Path(__file__).resolve().parents[3] / "review-input/us-politician-trades-watch/scripts"


def load(name: str):
    expected = {"process_snapshot": PROCESSOR_SHA256, "render_dashboard": RENDERER_SHA256}[name]
    path = SCRIPTS / f"{name}.py"
    if digest(path.read_bytes()) != expected:
        raise ValueError(f"Supplied baseline drift: {name}; review before changing pin")
    spec = importlib.util.spec_from_file_location(f"unison_baseline_{name}", path)
    module = importlib.util.module_from_spec(spec)
    if name == "render_dashboard":
        previous = sys.modules.get("process_snapshot")
        sys.modules["process_snapshot"] = load("process_snapshot")
        try:
            spec.loader.exec_module(module)
        finally:
            if previous is None:
                sys.modules.pop("process_snapshot", None)
            else:
                sys.modules["process_snapshot"] = previous
    else:
        spec.loader.exec_module(module)
    return module
