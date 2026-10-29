"""Recoverable publication of one House saved-paper artifact set.

The pending record is written before replacements. Controlled readers stop
until all files are installed; the next writer completes the same prepared
result without OCR. This is process-interruption recovery, not a database.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .codec import encode

_HELD = ContextVar("house_paper_review_locks", default=frozenset())
_PENDING = "house_clerk/paper_pending_materializations"
_SCHEMA = "house-paper-prepared-commit/v1"


def _inside(root: Path, path: Path) -> Path:
    target = path.resolve()
    if root not in target.parents:
        raise ValueError("House paper commit path escapes review root")
    return target


@contextmanager
def paper_review_access(review_root: Path, *, require_complete: bool = False):
    """Coordinate controlled readers/writers; OS releases the lock on exit/crash."""
    root = Path(review_root).resolve()
    if root in _HELD.get():
        if require_complete:
            require_complete_paper_commits(root)
        yield
        return
    # Readers keep source checkouts read-only. All processes for the same
    # canonical review path share an OS lock in the user's temporary runtime.
    runtime = Path(tempfile.gettempdir()).resolve()
    key = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    path = _inside(runtime, runtime / "unison-house-paper-locks" / (key + ".lock"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("House paper review is busy; retry after its writer or reader finishes") from error
        token = _HELD.set(_HELD.get() | {root})
        try:
            if require_complete:
                require_complete_paper_commits(root)
            yield
        finally:
            _HELD.reset(token)
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def require_complete_paper_commits(review_root: Path) -> None:
    if any((Path(review_root) / _PENDING).glob("*/*/*.json")):
        raise ValueError("House paper materialization is incomplete; resume its prepared commit before consumption")


def _relative(metadata: dict) -> Path:
    from .house_ptr_paper_temporal import validate_paper_source_metadata
    document, sha, _, _ = validate_paper_source_metadata(metadata)
    year = str(metadata["filing_year"])
    if not re.fullmatch(r"[0-9]{4}", year):
        raise ValueError("House prepared commit filing year is invalid")
    return Path(year) / document / (sha + ".json")


def _digest(path: Path) -> str | None:
    if path.exists() and not path.is_file():
        raise ValueError("House paper commit expected a file")
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".paper-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _targets(root: Path, metadata: dict, outputs: dict) -> dict:
    required = {"observation", "header", "extraction", "qualification"}
    if (not isinstance(outputs, dict) or not required <= set(outputs) <= required | {"legacy_reference"}
            or any(not isinstance(value, dict) for value in outputs.values())):
        raise ValueError("House prepared commit has an invalid artifact set")
    relative = _relative(metadata)
    folders = {"observation": "paper_observations", "header": "paper_amount_headers",
               "extraction": "extractions", "qualification": "qualifications"}
    paths = {key: _inside(root, root / "house_clerk" / folder / relative) for key, folder in folders.items()}
    if "legacy_reference" in outputs:
        saved = outputs["legacy_reference"]
        sha = hashlib.sha256(encode(saved.get("extraction"))).hexdigest()
        reference = {
            "path": (Path("house_clerk/paper_legacy_extraction_references") / relative.with_suffix("") / (sha + ".json")).as_posix(),
            "source_sha256": metadata["sha256"], "document_id": metadata["document_id"],
            "canonical_extraction_sha256": sha,
        }
        if 'source_bound_paper_owner_correction' in outputs['qualification']:
            from .house_paper_owner_correction import _legacy_reference
            reference = _legacy_reference(saved.get('extraction'), metadata)
        if (saved.get("schema_version") != "house-paper-legacy-extraction-reference/v1" or
                any(saved.get(k) != v for k, v in reference.items() if k != "path") or
                outputs["qualification"].get("legacy_extraction_reference") != reference):
            raise ValueError("House prepared legacy reference differs")
        paths["legacy_reference"] = _inside(root, root / reference["path"])
    return paths


def _validate(root: Path, evidence_root: Path, metadata: dict, prepared: dict) -> tuple[dict,dict]:
    from .house_paper_pipeline import preserved_paper_materialization
    if not isinstance(prepared, dict):
        raise ValueError("House prepared commit must be an object")
    payload = prepared.get("payload")
    if (prepared.get("schema_version") != _SCHEMA or not isinstance(payload, dict) or
            hashlib.sha256(encode(payload)).hexdigest() != prepared.get("payload_sha256") or
            payload.get("metadata") != metadata):
        raise ValueError("House prepared commit source or payload binding differs")
    outputs = payload.get("outputs")
    paths = _targets(root, metadata, outputs)
    before = payload.get("before_sha256")
    if (not isinstance(before, dict) or set(before) != set(paths) or
            any(v is not None and (not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)) for v in before.values())):
        raise ValueError("House prepared commit prior file binding differs")
    inputs = payload.get("input_sha256")
    input_paths = {name: _inside(root, root / "house_clerk" / folder / _relative(metadata)) for name, folder in (
        ("identity", "identities"), ("secondary_readings", "paper_key_field_rereads"))}
    if (not isinstance(inputs, dict) or set(inputs) != set(input_paths) or
            any(_digest(path) != inputs[name] for name, path in input_paths.items())):
        raise ValueError("House prepared commit input evidence changed")
    source_root = Path(evidence_root).resolve()
    source = (source_root / metadata["archive_path"]).resolve()
    if source_root not in source.parents or _digest(source) != metadata["sha256"]:
        raise ValueError("House prepared commit archived source differs")
    if (outputs["header"].get("source_sha256") != metadata["sha256"] or
            not preserved_paper_materialization(metadata, outputs["extraction"], outputs["qualification"], outputs["observation"])):
        raise ValueError("House prepared commit artifact binding differs")
    # Inspect every destination before the first replacement. A user edit or
    # foreign writer after interruption must never be silently overwritten.
    encoded = {key: encode(value) for key, value in outputs.items()}
    for key, path in paths.items():
        if _digest(path) not in {before[key], hashlib.sha256(encoded[key]).hexdigest()}:
            raise ValueError("House prepared commit destination changed outside the prepared result")
    return paths, encoded


def _replace_output(path: Path, content: bytes) -> None:
    if not path.is_file() or path.read_bytes() != content:
        _atomic(path, content)


def _finish(root: Path, evidence_root: Path, metadata: dict, pending: Path, prepared: dict) -> None:
    paths, encoded = _validate(root, evidence_root, metadata, prepared)
    for key in ("observation", "header", "legacy_reference", "extraction", "qualification"):
        if key in paths:
            _replace_output(paths[key], encoded[key])
    if any(path.read_bytes() != encoded[key] for key, path in paths.items()):
        raise ValueError("House prepared commit verification differs")
    pending.unlink()


def commit_paper_materialization(review_root: Path, evidence_root: Path, metadata: dict, outputs: dict) -> None:
    root = Path(review_root).resolve()
    with paper_review_access(root):
        paths = _targets(root, metadata, outputs)
        pending = _inside(root, root / _PENDING / _relative(metadata))
        if pending.exists():
            raise ValueError("House prepared commit already exists; recover it first")
        inputs = {name: _digest(_inside(root, root / "house_clerk" / folder / _relative(metadata))) for name, folder in (
            ("identity", "identities"), ("secondary_readings", "paper_key_field_rereads"))}
        payload = {"metadata": metadata, "outputs": outputs,
                   "before_sha256": {k: _digest(p) for k, p in paths.items()}, "input_sha256": inputs}
        prepared = {"schema_version": _SCHEMA, "payload": payload,
                    "payload_sha256": hashlib.sha256(encode(payload)).hexdigest()}
        _validate(root, evidence_root, metadata, prepared)
        _atomic(pending, encode(prepared))
        _finish(root, evidence_root, metadata, pending, prepared)


def recover_paper_materialization(review_root: Path, evidence_root: Path, metadata: dict) -> bool:
    root = Path(review_root).resolve()
    with paper_review_access(root):
        pending = _inside(root, root / _PENDING / _relative(metadata))
        if not pending.is_file():
            return False
        _finish(root, evidence_root, metadata, pending, json.loads(pending.read_bytes()))
        return True


def recover_pending_paper_materializations(evidence_root: Path, review_root: Path) -> list[Path]:
    """Default selection and batch resumption finish prepared results first."""
    root = Path(review_root).resolve()
    evidence = Path(evidence_root).resolve()
    recovered = []
    with paper_review_access(root):
        pending_root = root / _PENDING
        for pending in sorted(pending_root.glob("*/*/*.json")):
            metadata_path = _inside(evidence, evidence / "house_clerk/documents" / pending.relative_to(pending_root))
            metadata = json.loads(metadata_path.read_bytes())
            if _relative(metadata) != pending.relative_to(pending_root):
                raise ValueError("House prepared commit metadata path differs")
            if recover_paper_materialization(root, evidence, metadata):
                recovered.append(metadata_path)
    return recovered
