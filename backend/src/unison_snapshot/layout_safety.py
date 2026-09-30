"""Reject redirected publication directories before touching a worktree."""
from pathlib import Path


def reject_redirected_layout(root: Path, *, flat: tuple[str, ...] = (),
                             bucketed: tuple[str, ...] = ()) -> None:
    def check(path: Path) -> None:
        try:
            redirected = path.resolve() != path
        except (OSError, RuntimeError):
            redirected = True
        if redirected:
            raise ValueError(f"Publication layout directory is redirected: {path}")

    for name in (*flat, *bucketed):
        directory = root / name
        check(directory)
        if name in bucketed:
            for bucket in directory.glob("[0-9a-f][0-9a-f]"):
                check(bucket)
