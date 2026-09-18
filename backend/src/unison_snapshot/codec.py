"""Wire encoding shared by every content-addressed object."""
import hashlib
import json


def encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bucket(kind: str, key: str) -> str:
    if kind not in {"people", "tickers", "market"}:
        raise ValueError("Unknown shard class")
    if kind != "people":
        key = key.upper()
    return digest(f"{kind}\0{key}".encode("utf-8"))[:2]
