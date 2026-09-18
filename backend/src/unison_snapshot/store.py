"""Atomic local Git publication without touching the caller's working tree."""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile

from .builder import Bundle, LAYOUT, SCHEMA
from .codec import bucket, digest

SHA = re.compile(r"[0-9a-f]{40}")
PATH = re.compile(r"(?:manifest\.json|(?:people|tickers)/[0-9a-f]{2}/(?:index|[0-9a-f]{64})\.json|board/[0-9a-f]{64}\.json)")


class GitStore:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def git(self, *args: str, data: bytes | None = None, env: dict | None = None) -> bytes:
        result = subprocess.run(["git", "--git-dir", str(self.path), *args], input=data,
                                capture_output=True, env=env)
        if result.returncode:
            raise RuntimeError(f"Git operation failed ({args[0]}): {result.stderr.decode('utf-8', errors='replace').strip()}")
        return result.stdout

    def initialize(self) -> None:
        if self.path.exists():
            if not (self.path / "HEAD").is_file():
                raise ValueError("Refusing to initialize over an existing non-repository directory")
            if self.git("rev-parse", "--is-bare-repository").strip() != b"true":
                raise ValueError("Data store must be a bare repository")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["git", "init", "--bare", "--object-format=sha1", str(self.path)], capture_output=True)
        if result.returncode:
            raise RuntimeError("Unable to initialize local bare repository")

    def head(self) -> str | None:
        refs = self.git("for-each-ref", "--format=%(objectname)", "refs/heads/demo").decode().splitlines()
        return refs[0] if refs else None

    def paths(self, commit: str) -> set[str]:
        self._commit(commit)
        return set(self.git("ls-tree", "-r", "--name-only", commit).decode().splitlines())

    @staticmethod
    def _commit(commit: str) -> None:
        if not SHA.fullmatch(commit):
            raise ValueError("A frozen 40-hex commit is required")

    def read(self, commit: str, path: str) -> bytes:
        self._commit(commit)
        if not PATH.fullmatch(path):
            raise ValueError("Path outside snapshot contract")
        tag = self.git("rev-parse", "--verify", f"refs/tags/published/{commit}").decode().strip()
        if tag != commit:
            raise ValueError("Commit is not a published snapshot")
        return self.git("show", f"{commit}:{path}")

    def publish(self, bundle: Bundle, *, expected_head: str | None) -> str:
        """CAS the demo ref and publication tag together; old objects stay reachable."""
        if bundle.manifest.get("is_demo") is not True:
            raise ValueError("Only demo publication is enabled")
        if expected_head is not None:
            self._commit(expected_head)
        if self.head() != expected_head:
            raise ValueError("Concurrent publication detected; rebuild from current head")
        if any(not PATH.fullmatch(path) for path in bundle.files):
            raise ValueError("Bundle contains a forbidden path")
        if json.loads(bundle.files["manifest.json"]) != bundle.manifest:
            raise ValueError("Manifest bytes do not match manifest object")
        if expected_head:
            previous = json.loads(self.read(expected_head, "manifest.json"))
            comparable = dict(bundle.manifest, generated_at=previous.get("generated_at"))
            if comparable == previous and all(
                path == "manifest.json" or self.read(expected_head, path) == content
                for path, content in bundle.files.items()
            ):
                return expected_head
        # Validate content paths even if a caller constructed Bundle directly.
        for path, content in bundle.files.items():
            stem = Path(path).stem
            if len(stem) == 64 and digest(content) != stem:
                raise ValueError("Content address mismatch")
        with tempfile.TemporaryDirectory(prefix="unison-index-") as temp:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / "index"),
                       GIT_AUTHOR_NAME="Unison Demo Producer", GIT_AUTHOR_EMAIL="demo@invalid.example",
                       GIT_COMMITTER_NAME="Unison Demo Producer", GIT_COMMITTER_EMAIL="demo@invalid.example")
            if expected_head:
                self.git("read-tree", expected_head, env=env)
                old_paths = self.paths(expected_head)
            else:
                self.git("read-tree", "--empty", env=env)
                old_paths = set()
            for path in old_paths - bundle.files.keys():
                if path.endswith("/index.json"):
                    self.git("update-index", "--index-info", data=f"0 {'0' * 40}\t{path}\n".encode(), env=env)
            for path, content in sorted(bundle.files.items()):
                if path in old_paths and len(Path(path).stem) == 64:
                    if self.read(expected_head, path) != content:
                        raise ValueError("Immutable object collision")
                obj = self.git("hash-object", "-w", "--stdin", data=content).decode().strip()
                self.git("update-index", "--add", "--cacheinfo", "100644", obj, path, env=env)
            tree = self.git("write-tree", env=env).decode().strip()
            if expected_head and self.git("rev-parse", expected_head + "^{tree}").decode().strip() == tree:
                return expected_head
            parents = ["-p", expected_head] if expected_head else []
            commit = self.git("commit-tree", tree, *parents, data=b"Publish synthetic snapshot\n", env=env).decode().strip()
            old = expected_head or "0" * 40
            transaction = (f"start\nupdate refs/heads/demo {commit} {old}\n"
                           f"create refs/tags/published/{commit} {commit}\nprepare\ncommit\n")
            self.git("update-ref", "--stdin", data=transaction.encode())
            return commit

    def rollback(self, target: str, *, expected_head: str) -> None:
        self._commit(expected_head)
        manifest = json.loads(self.read(target, "manifest.json"))
        if manifest.get("is_demo") is not True:
            raise ValueError("Rollback requires a published demo snapshot")
        self.git("update-ref", "refs/heads/demo", target, expected_head)


def assemble(store: GitStore, commit: str, *, mode: str = "dashboard", key: str | None = None) -> dict:
    """Reference test harness, NOT the counterpart's unprovided snapshot_repo.py."""
    manifest = json.loads(store.read(commit, "manifest.json"))
    if manifest.get("schema_version") != SCHEMA or manifest.get("storage_layout") != LAYOUT:
        raise ValueError("Unsupported manifest contract")

    def shard(path: str, sha: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("Invalid shard address")
        content = store.read(commit, f"{path}/{sha}.json")
        if digest(content) != sha:
            raise ValueError("Corrupt shard")
        return json.loads(content)

    if mode == "dashboard":
        board = shard("board", manifest["board"])
    elif mode in {"people", "tickers"} and key:
        if mode == "tickers":
            key = key.upper()
        path = f"{mode}/{bucket(mode, key)}"
        index = json.loads(store.read(commit, f"{path}/index.json"))
        value = shard(path, index["shards"][key])
        if value.pop("requires", []):
            raise ValueError("Market dependencies are not enabled in this prototype")
        if mode == "people":
            value["people"] = [value.pop("person")]
        board = {**value, "security_market_data": []}
    else:
        raise ValueError("Unsupported selection")
    excluded = {"board", "source_health"}
    meta = {name: value for name, value in manifest.items() if name not in excluded}
    meta["selection_scope"] = {"mode": mode, "key": key, "universe_complete": False}
    return {"meta": meta, **board, "source_health": manifest.get("source_health", [])}
