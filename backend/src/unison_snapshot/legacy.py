"""Pinned supplied processor validation, not a substitute for new consumer tests."""
import importlib.util
from pathlib import Path
import sys

from .codec import digest

PROCESSOR_SHA256 = "a2cd37cbf447f591bf11f7f397f0fcb817ba1032f6ca5c9ca0efb967f50c7a4b"
PROCESSOR_V2_SHA256 = "746e60177d65dc40504cb0bbd752a28c510627be6039e724b112ed6d46495c15"
PROCESSOR_V3_SHA256 = "26c2003d2706c1ce306531a975dfa1f03f22541cce73316277ccf133d7079e6c"
RENDERER_V3_SHA256 = "6ebe5771d577b977cc531369f3ee6c916c6235789887b75b608f2875cf27fe2f"
RENDERER_SHA256 = "ae3fc8a38cdc1df7c7ddff3b9f3f834401763d09d5b9891e56022b786f3f6c0c"
SCRIPTS = Path(__file__).resolve().parents[3] / "review-input/us-politician-trades-watch/scripts"
V2_SCRIPTS = Path(__file__).resolve().parents[3] / "frontend-contract-v2/scripts"
V3_SCRIPTS = Path(__file__).resolve().parents[3] / "frontend-contract-v3/scripts"


def processor_version_for(payload: dict) -> str:
    meta = payload.get("meta", {})
    if "holding_report_coverage" in meta or 'transaction_disclosure_relations' in meta or any(
            str(row.get('id','')).startswith(('senate-annual-part4b:','house-annual-b:','oge-annual-row:')) for row in payload.get('transactions',[])) or meta.get("amount_policy") == "bounded-and-open-separate/v1" or any(
            row.get(prefix + "_high") is None and type(row.get(prefix + "_low_exclusive")) is bool
            for kind, prefix in (("transactions", "amount"), ("reported_holdings", "value"))
            for row in payload.get(kind, [])):
        return "v3"
    return "v2"


def load(name: str, *, version: str = "v1"):
    if version not in {"v1", "v2", "v3"}:
        raise ValueError(f"Unknown consumer version: {version}")
    expected = (RENDERER_V3_SHA256 if name == "render_dashboard" and version == "v3"
                else PROCESSOR_V3_SHA256 if name == "process_snapshot" and version == "v3"
                else PROCESSOR_V2_SHA256 if name == "process_snapshot" and version == "v2"
                else {"process_snapshot": PROCESSOR_SHA256,
                      "render_dashboard": RENDERER_SHA256}[name])
    path = (V3_SCRIPTS if version == "v3" else
            V2_SCRIPTS if name == "process_snapshot" and version == "v2" else SCRIPTS) / f"{name}.py"
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
    if name == 'render_dashboard':
        from .known_gaps import render
        from .source_freshness import render as render_freshness
        original = module.render_html
        module.render_html = lambda data: render_freshness(render(original(data), data), data)
    return module
