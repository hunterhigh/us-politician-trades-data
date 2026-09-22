"""Pinned supplied processor validation, not a substitute for new consumer tests."""
import importlib.util
from pathlib import Path
import sys

from .codec import digest

PROCESSOR_SHA256 = "a2cd37cbf447f591bf11f7f397f0fcb817ba1032f6ca5c9ca0efb967f50c7a4b"
PROCESSOR_V2_SHA256 = "a1b86307dfe4aef3e4afda6f3f5b954cf18782edfc94edfc202d803be578456c"
RENDERER_SHA256 = "ae3fc8a38cdc1df7c7ddff3b9f3f834401763d09d5b9891e56022b786f3f6c0c"
SCRIPTS = Path(__file__).resolve().parents[3] / "review-input/us-politician-trades-watch/scripts"
V2_SCRIPTS = Path(__file__).resolve().parents[3] / "frontend-contract-v2/scripts"


def load(name: str, *, version: str = "v1"):
    if version not in {"v1", "v2"}:
        raise ValueError(f"Unknown consumer version: {version}")
    expected = (PROCESSOR_V2_SHA256 if name == "process_snapshot" and version == "v2"
                else {"process_snapshot": PROCESSOR_SHA256,
                      "render_dashboard": RENDERER_SHA256}[name])
    path = (V2_SCRIPTS if name == "process_snapshot" and version == "v2" else SCRIPTS) / f"{name}.py"
    if digest(path.read_bytes()) != expected:
        raise ValueError(f"Supplied baseline drift: {name}; review before changing pin")
    spec = importlib.util.spec_from_file_location(f"unison_{version}_{name}", path)
    module = importlib.util.module_from_spec(spec)
    if name == "render_dashboard":
        previous = sys.modules.get("process_snapshot")
        sys.modules["process_snapshot"] = load("process_snapshot", version=version)
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
