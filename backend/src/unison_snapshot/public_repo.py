"""Read the hash-sharded contract directly from a public GitHub repository."""
from dataclasses import dataclass
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .builder import LAYOUT, SCHEMA
from .builder import timestamp
from .codec import bucket

SHA1 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
LIMITS = {"manifest": 16 * 1024, "index": 8 * 1024, "shard": 8 * 1024 * 1024}


class PublicSnapshotError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicSnapshotError("Repository transport returned a redirect")


class HTTPTransport:
    def __init__(self, token: str | None = None, timeout: float = 20.0):
        self.token, self.timeout = token, timeout
        self.opener = urllib.request.build_opener(NoRedirect)

    def get(self, url: str, limit: int, *, api: bool = False) -> bytes:
        headers = {"Accept": "application/vnd.github+json" if api else "application/json",
                   "User-Agent": "unison-public-snapshot/0.2"}
        if self.token and api:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise PublicSnapshotError(f"Repository returned HTTP {response.status}")
                data = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise PublicSnapshotError(f"Repository returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise PublicSnapshotError("Public repository is unavailable") from None
        if len(data) > limit:
            raise PublicSnapshotError("Repository object exceeds the contract size limit")
        return data


@dataclass(frozen=True)
class SnapshotSelection:
    commit: str
    snapshot: dict


class PublicSnapshotRepository:
    def __init__(self, owner: str, repo: str, *, ref: str = "main", transport=None):
        if not NAME.fullmatch(owner) or not NAME.fullmatch(repo) or ref not in {"main", "demo"}:
            raise ValueError("Invalid fixed public repository identity or ref")
        self.owner, self.repo, self.ref = owner, repo, ref
        self.transport = transport or HTTPTransport()

    def _json(self, data: bytes, label: str) -> dict:
        try:
            value = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PublicSnapshotError(f"{label} is not valid UTF-8 JSON") from None
        if not isinstance(value, dict):
            raise PublicSnapshotError(f"{label} must be an object")
        return value

    def resolve(self) -> str:
        ref = urllib.parse.quote(self.ref, safe="")
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/git/ref/heads/{ref}"
        value = self._json(self.transport.get(url, LIMITS["manifest"], api=True), "commit response")
        reference = value.get("object")
        commit = reference.get("sha") if isinstance(reference, dict) and reference.get("type") == "commit" else None
        if not isinstance(commit, str) or not SHA1.fullmatch(commit):
            raise PublicSnapshotError("GitHub did not return a frozen commit")
        return commit

    def _read(self, commit: str, path: str, limit: int) -> bytes:
        if not SHA1.fullmatch(commit) or not re.fullmatch(r"[a-z0-9./^-]+", path) or ".." in path:
            raise PublicSnapshotError("Invalid frozen repository path")
        url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{commit}/{path}"
        return self.transport.get(url, limit, api=False)

    def manifest(self, commit: str) -> dict:
        value = self._json(self._read(commit, "manifest.json", LIMITS["manifest"]), "manifest")
        required = {"schema_version", "storage_layout", "snapshot_id", "generated_at", "data_cutoff_at", "board"}
        if required - value.keys() or value.get("schema_version") != SCHEMA or value.get("storage_layout") != LAYOUT:
            raise PublicSnapshotError("Unsupported or incomplete manifest")
        if not SHA256.fullmatch(str(value.get("board", ""))) or not isinstance(value.get("snapshot_id"), str):
            raise PublicSnapshotError("Manifest addresses are invalid")
        demo = value.get("is_demo") is True
        if (self.ref == "demo") != demo:
            raise PublicSnapshotError("Demo manifest/ref boundary violated")
        try:
            timestamp(value["generated_at"])
            timestamp(value["data_cutoff_at"])
        except (TypeError, ValueError):
            raise PublicSnapshotError("Manifest timestamps are invalid") from None
        health = value.get("source_health", [])
        if not isinstance(health, list) or any(not isinstance(row, dict) for row in health):
            raise PublicSnapshotError("Manifest source_health must be an array")
        market = value.get("market_commit")
        if market is not None and (not isinstance(market, str) or not SHA1.fullmatch(market)):
            raise PublicSnapshotError("Manifest market_commit is invalid")
        return value

    def _content(self, commit: str, prefix: str, sha: str) -> dict:
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise PublicSnapshotError("Invalid content address")
        data = self._read(commit, f"{prefix}/{sha}.json", LIMITS["shard"])
        if hashlib.sha256(data).hexdigest() != sha:
            raise PublicSnapshotError("Content hash mismatch")
        return self._json(data, "content shard")

    def _entity(self, commit: str, kind: str, key: str) -> dict:
        original = key
        if kind in {"tickers", "market"}:
            key = key.upper()
        prefix = f"{kind}/{bucket(kind, key)}"
        index = self._json(self._read(commit, f"{prefix}/index.json", LIMITS["index"]), "entity index")
        shards = index.get("shards")
        if not isinstance(shards, dict):
            raise PublicSnapshotError("Entity index has no shards map")
        selected = shards.get(key, shards.get(original))
        if selected is None:
            raise PublicSnapshotError(f"No published {kind} record for {key}")
        return self._content(commit, prefix, selected)

    def _market(self, manifest: dict, requirements: object) -> list[dict]:
        if not isinstance(requirements, list) or len(requirements) > 40 or len(set(requirements)) != len(requirements):
            raise PublicSnapshotError("Invalid dependency plan")
        market_commit = manifest.get("market_commit")
        if requirements and not market_commit:
            raise PublicSnapshotError("Market dependencies are unavailable")
        rows = []
        for requirement in requirements:
            match = re.fullmatch(r"market:([A-Z0-9][A-Z0-9.\-^/]{0,31})", str(requirement))
            if not match:
                raise PublicSnapshotError("Invalid dependency declaration")
            value = self._entity(market_commit, "market", match.group(1))
            row = value.get("security_market_data", value)
            if isinstance(row, list) and len(row) == 1:
                row = row[0]
            if not isinstance(row, dict) or row.get("ticker") != match.group(1):
                raise PublicSnapshotError("Market shard does not match its dependency")
            rows.append(row)
        return rows

    def fetch(self, mode: str = "dashboard", key: str | None = None) -> SnapshotSelection:
        commit = self.resolve()
        manifest = self.manifest(commit)
        if mode in {"dashboard", "search"}:
            board = self._content(commit, "board", manifest["board"])
        elif mode == "person" and key:
            entity = self._entity(commit, "people", key)
            person = entity.get("person")
            if person is None and isinstance(entity.get("people"), list) and len(entity["people"]) == 1:
                person = entity["people"][0]
            if not isinstance(person, dict) or person.get("id") != key:
                raise PublicSnapshotError("Person shard identity mismatch")
            board = {"people": [person], "transactions": entity.get("transactions"),
                     "reported_holdings": entity.get("reported_holdings"),
                     "security_market_data": self._market(manifest, entity.get("requires"))}
        elif mode == "ticker" and key:
            key = key.upper()
            entity = self._entity(commit, "tickers", key)
            board = {"people": entity.get("people"), "transactions": entity.get("transactions"),
                     "reported_holdings": entity.get("reported_holdings"),
                     "security_market_data": self._market(manifest, entity.get("requires"))}
        else:
            raise PublicSnapshotError("Unsupported or incomplete selection")
        for field in ("people", "transactions", "reported_holdings", "security_market_data"):
            if not isinstance(board.get(field), list) or any(not isinstance(row, dict) for row in board[field]):
                raise PublicSnapshotError(f"Assembled {field} must be an array of objects")
        people = {row.get("id") for row in board["people"]}
        if None in people or any(row.get("person_id") not in people for name in ("transactions", "reported_holdings") for row in board[name]):
            raise PublicSnapshotError("Assembled disclosure references a missing person")
        excluded = {"board", "source_health"}
        meta = {name: value for name, value in manifest.items() if name not in excluded}
        meta.update(snapshot_commit=commit, selection_scope={"mode": mode, "key": key,
                    "universe_complete": manifest.get("coverage", {}).get("universe_complete", False)})
        snapshot = {"meta": meta, **board, "source_health": manifest.get("source_health", [])}
        return SnapshotSelection(commit, snapshot)
