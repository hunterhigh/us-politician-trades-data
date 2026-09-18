"""Safely prepare a Git working tree for one atomic snapshot commit."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import tempfile

from .builder import Bundle

MUTABLE = re.compile(r"(?:manifest\.json|(?:people|tickers)/[0-9a-f]{2}/index\.json)")
IMMUTABLE = re.compile(r"(?:(?:people|tickers)/[0-9a-f]{2}|board)/[0-9a-f]{64}\.json")


@dataclass(frozen=True)
class MaterializeResult:
    changed: bool
    business_changed: bool
    written: tuple[str, ...]
    removed: tuple[str, ...]


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def materialize(root: Path, bundle: Bundle) -> MaterializeResult:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(not (MUTABLE.fullmatch(path) or IMMUTABLE.fullmatch(path)) for path in bundle.files):
        raise ValueError("Bundle contains a path outside the public snapshot contract")
    previous = None
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Existing manifest is invalid; refusing to publish over it") from None
    written: list[str] = []
    for relative, content in sorted(bundle.files.items()):
        if relative == "manifest.json":
            continue
        target = root / relative
        if IMMUTABLE.fullmatch(relative) and target.exists() and target.read_bytes() != content:
            raise ValueError(f"Immutable snapshot object changed in place: {relative}")
        if not target.exists() or target.read_bytes() != content:
            _atomic(target, content)
            written.append(relative)
    expected_indexes = {path for path in bundle.files if MUTABLE.fullmatch(path) and path != "manifest.json"}
    removed: list[str] = []
    for kind in ("people", "tickers"):
        directory = root / kind
        if not directory.exists():
            continue
        for existing in directory.glob("[0-9a-f][0-9a-f]/index.json"):
            relative = existing.relative_to(root).as_posix()
            if relative not in expected_indexes:
                existing.unlink()
                removed.append(relative)
    manifest_bytes = bundle.files["manifest.json"]
    only_generation_time_changed = False
    if previous is not None and not written and not removed:
        comparable = dict(bundle.manifest, generated_at=previous.get("generated_at"))
        only_generation_time_changed = comparable == previous
    if not only_generation_time_changed and (not manifest_path.exists() or manifest_path.read_bytes() != manifest_bytes):
        _atomic(manifest_path, manifest_bytes)  # Manifest is the final local write.
        written.append("manifest.json")
    business_changed = previous is None or previous.get("snapshot_id") != bundle.manifest["snapshot_id"]
    return MaterializeResult(bool(written or removed), business_changed, tuple(written), tuple(removed))
