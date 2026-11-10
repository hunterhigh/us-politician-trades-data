"""Atomic output shared by command adapters."""
from pathlib import Path
import tempfile
from ..codec import encode

def _write_atomic(path: Path, payload: dict) -> None:
    encoded = encode(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                                     delete=False) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)
