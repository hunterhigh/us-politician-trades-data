"""Stable import surface for the public GitHub snapshot transport."""
from unison_snapshot.public_repo import (
    HTTPTransport,
    PublicSnapshotError,
    PublicSnapshotRepository,
    SnapshotSelection,
)

__all__ = ["HTTPTransport", "PublicSnapshotError", "PublicSnapshotRepository", "SnapshotSelection"]
