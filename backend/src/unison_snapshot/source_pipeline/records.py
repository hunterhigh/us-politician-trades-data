"""Shared immutable evidence and metadata storage with source-owned policies."""
import hashlib
import json
from pathlib import Path
import tempfile


def json_bytes(value, *, newline=False):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (raw + ("\n" if newline else "")).encode("utf-8")


def read_json(path, description, error):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise error(f"{description} is invalid") from None
    if not isinstance(value, dict):
        raise error(f"{description} must be an object")
    return value


def write_once(path, content, *, error, conflict, prefix=None, atomic=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise error(conflict)
        return
    if not atomic:
        path.write_bytes(content)
        return
    with tempfile.NamedTemporaryFile(dir=path.parent,
            prefix=prefix or f".{path.name}.", suffix=".tmp", delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def store_metadata(path, metadata, *, stable, error, conflict, invalid=None,
                   writer=None, prefix=None, reuse_existing=True, encode_first=False):
    encoded = json_bytes(metadata) if encode_first else None
    if path.exists():
        if invalid is None:
            existing = json.loads(path.read_text(encoding="utf-8"))
        else:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                raise error(invalid) from None
        if any(existing.get(key) != metadata[key] for key in stable):
            raise error(conflict)
        return existing if reuse_existing else metadata
    if encoded is None:
        encoded = json_bytes(metadata)
    if writer:
        writer(path, encoded)
    else:
        write_once(path, encoded, error=error, conflict=conflict, prefix=prefix)
    return metadata


def archive_record(root, namespace, content, extension, describe, *, error, collision,
                   stable, conflict, invalid=None, metadata_suffix=".json", writer=None,
                   reuse_existing=True, encode_first=False):
    root = root.resolve()
    sha = hashlib.sha256(content).hexdigest()
    folder = root.joinpath(*map(str, namespace))
    path = folder / (sha + extension)
    prefix = f".{sha}."
    if writer:
        writer(path, content)
    else:
        write_once(path, content, error=error, conflict=collision, prefix=prefix)
    metadata = describe(sha, path, root)
    keys = stable(metadata) if callable(stable) else stable
    return store_metadata(folder / (sha + metadata_suffix), metadata,
        stable=keys, error=error, conflict=conflict, invalid=invalid,
        writer=writer, prefix=prefix, reuse_existing=reuse_existing, encode_first=encode_first)
