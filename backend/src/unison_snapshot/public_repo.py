"""Read the hash-sharded contract directly from a public GitHub repository."""
from dataclasses import dataclass
from collections import OrderedDict
import hashlib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .builder import LAYOUT, SCHEMA
from .builder import timestamp
from .codec import bucket, encode
from .disclosure_status import (MAX_STATUS_BYTES, validate_reference, validate_summary,
                                validate_page, validate_scope_records)

SHA1 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
LIMITS = {"manifest": 16 * 1024, "index": 8 * 1024,
          "shard": 16 * 1024 * 1024, "board": 24 * 1024 * 1024}


class PublicSnapshotError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicSnapshotError("Repository transport returned a redirect")


class HTTPTransport:
    def __init__(self, token: str | None = None, timeout: float = 20.0,
                 retries: int = 2, backoff: float = 0.5):
        if retries < 0 or backoff < 0:
            raise ValueError("Retry settings must be non-negative")
        self.token, self.timeout = token, timeout
        self.retries, self.backoff = retries, backoff
        self.opener = urllib.request.build_opener(NoRedirect)

    def get(self, url: str, limit: int, *, api: bool = False) -> bytes:
        headers = {"Accept": "application/vnd.github+json" if api else "application/json",
                   "User-Agent": "unison-public-snapshot/0.2"}
        if self.token and api:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        request = urllib.request.Request(url, headers=headers, method="GET")
        retryable_http = {429, 500, 502, 503, 504}
        network_errors = (urllib.error.URLError, TimeoutError, ConnectionError,
                          http.client.HTTPException, OSError)
        for attempt in range(self.retries + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    if response.status != 200:
                        raise PublicSnapshotError(f"Repository returned HTTP {response.status}")
                    data = response.read(limit + 1)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in retryable_http or attempt == self.retries:
                    raise PublicSnapshotError(f"Repository returned HTTP {exc.code}") from None
            except network_errors:
                if attempt == self.retries:
                    raise PublicSnapshotError("Public repository is unavailable") from None
            if self.backoff:
                time.sleep(self.backoff * (2 ** attempt))
        if len(data) > limit:
            raise PublicSnapshotError("Repository object exceeds the contract size limit")
        return data


@dataclass(frozen=True)
class SnapshotSelection:
    commit: str
    snapshot: dict


class PublicSnapshotRepository:
    def __init__(self, owner: str, repo: str, *, ref: str = "main", transport=None,
                 cache_bytes: int = 32 * 1024 * 1024):
        if not NAME.fullmatch(owner) or not NAME.fullmatch(repo) or ref not in {"main", "demo"}:
            raise ValueError("Invalid fixed public repository identity or ref")
        self.owner, self.repo, self.ref = owner, repo, ref
        self.transport = transport or HTTPTransport()
        if type(cache_bytes) is not int or cache_bytes < 0:
            raise ValueError("Cache size must be a nonnegative integer")
        self._cache_budget = cache_bytes
        self._cache_size = 0
        self._cache = OrderedDict()

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
        # Cache immutable bytes only. Ref resolution and decoded caller objects
        # remain fresh, and every consumer still checks its size/hash contract.
        data = self._cache.get(url)
        if data is not None:
            self._cache.move_to_end(url)
        else:
            data = self.transport.get(url, limit, api=False)
        if len(data) > limit:
            raise PublicSnapshotError("Repository object exceeds the contract size limit")
        if url not in self._cache and len(data) <= self._cache_budget:
            while self._cache and self._cache_size + len(data) > self._cache_budget:
                _, evicted = self._cache.popitem(last=False)
                self._cache_size -= len(evicted)
            self._cache[url] = data
            self._cache_size += len(data)
        return data

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
        pages = value.get("market_pages", [])
        if not isinstance(pages, list) or len(pages) > 100 \
                or any(not isinstance(item, str) or not SHA256.fullmatch(item) for item in pages) \
                or len(set(pages)) != len(pages):
            raise PublicSnapshotError("Manifest market_pages is invalid")
        if bool(market) != bool(pages):
            raise PublicSnapshotError("Manifest market commit and pages must be declared together")
        if "disclosure_status" in value:
            try:
                validate_reference(value["disclosure_status"])
            except ValueError as exc:
                raise PublicSnapshotError(str(exc)) from None
        return value

    def disclosure_status(self, commit: str, *, include_records: bool = False) -> dict | None:
        """Read status from the caller's fixed publication; old publications return None."""
        manifest = self.manifest(commit)
        reference = manifest.get("disclosure_status")
        if reference is None:
            return None

        def read(ref: dict) -> dict:
            data = self._read(commit, f"status/{ref['sha256']}.json", MAX_STATUS_BYTES)
            if len(data) != ref["byte_length"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
                raise PublicSnapshotError("Disclosure status byte length or hash mismatch")
            return self._json(data, "disclosure status")

        summary = read(reference)
        try:
            validate_summary(summary, manifest)
            if include_records:
                for source in summary["sources"]:
                    records = []
                    for page in source["pages"]:
                        records.extend(validate_page(read(page), source, page)["records"])
                    validate_scope_records(source, records)
                    source["records"] = records
        except ValueError as exc:
            raise PublicSnapshotError(str(exc)) from None
        return summary

    def _content(self, commit: str, prefix: str, sha: str) -> dict:
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise PublicSnapshotError("Invalid content address")
        limit = LIMITS["board"] if prefix == "board" else LIMITS["shard"]
        data = self._read(commit, f"{prefix}/{sha}.json", limit)
        if hashlib.sha256(data).hexdigest() != sha:
            raise PublicSnapshotError("Content hash mismatch")
        return self._json(data, "content shard")

    def _entity_address(self, commit: str, kind: str, key: str) -> tuple[str, str]:
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
        return prefix, selected

    def _entity(self, commit: str, kind: str, key: str) -> dict:
        prefix, selected = self._entity_address(commit, kind, key)
        return self._content(commit, prefix, selected)

    def _market(self, manifest: dict, requirements: object) -> list[dict]:
        if not isinstance(requirements, list) or len(requirements) > 2000 or len(set(requirements)) != len(requirements):
            raise PublicSnapshotError("Invalid dependency plan")
        market_commit = manifest.get("market_commit")
        if requirements and not market_commit:
            raise PublicSnapshotError("Market dependencies are unavailable")
        tickers = []
        for requirement in requirements:
            match = re.fullmatch(r"market:([A-Z0-9][A-Z0-9.\-^/]{0,31})", str(requirement))
            if not match:
                raise PublicSnapshotError("Invalid dependency declaration")
            tickers.append(match.group(1))
        pages = manifest.get("market_pages", [])
        # Broad person views can otherwise make thousands of serial requests.
        # Existing bulk pages are useful only when many dependencies are needed.
        bulk = ({row["ticker"]: row for row in self._market_pages(manifest)}
                if pages and len(tickers) > max(100, 4 * len(pages)) else {})
        rows = []
        for ticker in tickers:
            value = None
            if ticker in bulk:
                prefix, sha = self._entity_address(market_commit, "market", ticker)
                candidate = {"security_market_data": [bulk[ticker]]}
                # Prove the page row matches the entity address too. A mismatch
                # uses the original shard reader, including its hash validation.
                if hashlib.sha256(encode(candidate)).hexdigest() == sha:
                    value = candidate
            if value is None:
                value = self._entity(market_commit, "market", ticker)
            row = value.get("security_market_data", value)
            if isinstance(row, list) and len(row) == 1:
                row = row[0]
            if not isinstance(row, dict) or row.get("ticker") != ticker:
                raise PublicSnapshotError("Market shard does not match its dependency")
            rows.append(row)
        return rows

    def _market_pages(self, manifest: dict) -> list[dict]:
        market_commit = manifest.get("market_commit")
        pages = manifest.get("market_pages", [])
        if not pages:
            return []
        rows: list[dict] = []
        seen: set[str] = set()
        for sha in pages:
            value = self._content(market_commit, "market-pages", sha)
            page = value.get("security_market_data")
            if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                raise PublicSnapshotError("Market page has invalid rows")
            for row in page:
                ticker = row.get("ticker")
                if not isinstance(ticker, str) or ticker in seen:
                    raise PublicSnapshotError("Market pages contain invalid or duplicate tickers")
                seen.add(ticker)
                rows.append(row)
        return rows

    @staticmethod
    def _normalized_person_name(value: object) -> str:
        return " ".join(value.split()).casefold() if isinstance(value, str) else ""

    def _resolve_person_key(self, commit: str, manifest: dict, value: str) -> str:
        """Resolve an exact display/short name inside the same frozen snapshot commit."""
        if ":" in value:
            return value
        requested = self._normalized_person_name(value)
        if not requested:
            raise PublicSnapshotError("Person name is empty")
        board = self._content(commit, "board", manifest["board"])
        people = board.get("people")
        if not isinstance(people, list):
            raise PublicSnapshotError("Board has no people array for name resolution")
        matches = []
        for person in people:
            if not isinstance(person, dict) or not isinstance(person.get("id"), str):
                raise PublicSnapshotError("Board contains an invalid person")
            aliases = {self._normalized_person_name(person.get("display_name")),
                       self._normalized_person_name(person.get("short_name"))}
            if requested in aliases:
                matches.append(person["id"])
        matches = sorted(set(matches))
        if len(matches) != 1:
            reason = "ambiguous" if matches else "not found"
            raise PublicSnapshotError(f"Person name is {reason} in the frozen snapshot")
        return matches[0]

    def fetch(self, mode: str = "dashboard", key: str | None = None) -> SnapshotSelection:
        commit = self.resolve()
        manifest = self.manifest(commit)
        if mode in {"dashboard", "search"}:
            board = self._content(commit, "board", manifest["board"])
            board["security_market_data"] = self._market_pages(manifest)
        elif mode == "person" and key:
            key = self._resolve_person_key(commit, manifest, key)
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
