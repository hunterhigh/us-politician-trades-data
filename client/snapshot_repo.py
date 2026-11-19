"""Deprecated public-reader compatibility exports, retained for offline consumers."""
from unison_snapshot.public_repo import (
    HTTPTransport,
    PublicSnapshotError,
    PublicSnapshotRepository,
    SnapshotSelection,
)

__all__ = ["HTTPTransport", "PublicSnapshotError", "PublicSnapshotRepository", "SnapshotSelection"]
