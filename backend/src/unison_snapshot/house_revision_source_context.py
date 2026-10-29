"""Read-only original-bound source context for House amendment investigation.

This enumerates sources before consulting review identity or transaction IDs.
An observed context is not a complete source scope, financial qualification or
revision permission. A caller still needs independent glyph/paint, physical-role
and full native projection proofs. No OCR, collection or source writes occur.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re

from .house import INDEX_URL, MAX_PDF_BYTES, MAX_ZIP_BYTES, document_url, parse_index
from .house_members import MAX_MEMBERS_BYTES, MEMBERS_URL, parse_members, suggest_identity

VERSION = "house-revision-source-context/v1"
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_PATHS = 100_000
MAX_INDEX_VERSIONS = 512
MAX_ROSTERS = 128
MAX_INDEX_ROWS = 200_000
MAX_IDENTITY_EVALUATIONS = 200_000
MAX_REPORTS = 512
MAX_PAGES = 32
MAX_TOTAL_CHARACTERS = 100_000
SHA = re.compile(r"[a-f0-9]{64}")
PERSON = re.compile(r"house:[A-Z][0-9]{6}")
PTR_URL = re.compile(r"https://disclosures-clerk\.house\.gov/public_disc/ptr-pdfs/([0-9]{4})/([0-9]{1,20})\.pdf")


class _Unavailable(Exception):
    pass


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _json(content):
    def invalid_constant(value):
        raise ValueError(value)
    value = json.loads(content, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


def _time(value):
    if not isinstance(value, str):
        raise ValueError("missing time")
    time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if time.tzinfo is None:
        raise ValueError("time without zone")
    return time.astimezone(timezone.utc)


class _Reader:
    def __init__(self, root, label, budget):
        self.root = Path(root).resolve()
        self.label = label
        self.dependencies = {}
        self.total = 0
        self.budget = budget

    def path(self, relative):
        target = self.root / relative
        try:
            target.resolve().relative_to(self.root)
        except (OSError, ValueError):
            raise _Unavailable("source_path_outside_root") from None
        return target

    def paths(self, pattern):
        self.path(pattern.split("*", 1)[0].rstrip("/"))
        result = []
        for path in self.root.glob(pattern):
            if len(result) >= MAX_PATHS:
                raise _Unavailable("source_path_budget_exceeded")
            relative = path.relative_to(self.root).as_posix()
            self.path(relative)
            self.budget["paths"].add((self.label, relative))
            if len(self.budget["paths"]) > MAX_PATHS:
                raise _Unavailable("source_path_budget_exceeded")
            result.append(relative)
        return sorted(result)

    def read(self, relative, limit):
        path = self.path(relative)
        try:
            if path.stat().st_size > limit:
                raise _Unavailable("source_object_byte_budget_exceeded")
            with path.open("rb") as handle:
                content = handle.read(limit + 1)
        except OSError:
            raise _Unavailable("source_object_missing") from None
        if len(content) > limit:
            raise _Unavailable("source_object_byte_budget_exceeded")
        dependency = {"root": self.label, "path": relative, "sha256": _digest(content), "byte_length": len(content)}
        previous = self.dependencies.get(relative)
        if previous is not None and previous != dependency:
            raise _Unavailable("source_changed_during_context_read")
        if previous is None:
            if self.budget["bytes"] + len(content) > MAX_TOTAL_BYTES:
                raise _Unavailable("source_total_byte_budget_exceeded")
            self.total += len(content)
            self.budget["bytes"] += len(content)
            self.dependencies[relative] = dependency
        return content

    def object(self, relative, limit=MAX_JSON_BYTES):
        raw = self.read(relative, limit)
        try:
            return _json(raw), raw
        except (ValueError, UnicodeError):
            raise _Unavailable("source_json_invalid") from None


@dataclass(frozen=True)
class HouseRevisionSourceDocument:
    """Immutable original and cache bytes; cache bytes are not source proofs."""
    filing_year: int
    document_id: str
    source_sha256: str
    metadata_bytes: bytes
    pdf_bytes: bytes
    extraction_bytes: bytes | None
    qualification_bytes: bytes | None
    planned_ocr_pages: tuple[int, ...]

    @property
    def metadata(self):
        return _json(self.metadata_bytes)

    @property
    def extraction(self):
        return _json(self.extraction_bytes) if self.extraction_bytes is not None else None

    @property
    def qualification(self):
        return _json(self.qualification_bytes) if self.qualification_bytes is not None else None


class HouseRevisionSourceContext:
    """Snapshot diagnostics only. Never a serializable passed/eligible token."""
    __slots__ = ("_provenance_bytes", "_documents")
    scope_complete = False
    review_projection_proven = False

    def __init__(self, provenance, documents):
        self._provenance_bytes = json.dumps(provenance, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self._documents = {person: tuple(items) for person, items in documents.items()}

    @property
    def status(self):
        return self.provenance()["status"]

    @property
    def reasons(self):
        return self.provenance()["reasons"]

    def provenance(self):
        return _json(self._provenance_bytes)

    def for_person(self, person_id):
        provenance = self.provenance()
        person = provenance["people"].get(person_id)
        if person is not None:
            person["reasons"] = sorted(set(person["reasons"]) | set(provenance["reasons"]))
            person["excluded_sources"] = [item for item in provenance["source_person_memberships"]
                if item["person_id"] != person_id]
        return person

    def documents_for(self, person_id):
        return self._documents.get(person_id, ())


def _native_plan(content, source_sha):
    """Bounded actual native observation; no saved words or OCR execution."""
    try:
        import pdfplumber
        from pdfminer.pdfexceptions import PDFException
    except ImportError:
        raise _Unavailable("source_native_dependencies_unavailable") from None
    from .pdf_native_observer import observe_source_characters
    from .house_ptr import observe_native_word_pages, planned_ocr_pages
    try:
        with pdfplumber.open(io.BytesIO(content)) as document:
            if not 1 <= len(document.pages) <= MAX_PAGES:
                raise _Unavailable("source_page_budget_exceeded")
            count = 0
            # Preflight the controlled aggregator before ordinary word/geometry
            # materialization. Never turn a failed inventory into no-OCR evidence.
            for page in document.pages:
                characters = observe_source_characters(page)[0]
                count += len(characters)
                if count > MAX_TOTAL_CHARACTERS:
                    raise _Unavailable("source_character_budget_exceeded")
            pages = observe_native_word_pages(document, source_sha)
            return planned_ocr_pages(pages), len(pages)
    except (PDFException, OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError):
        raise _Unavailable("source_native_inventory_unavailable") from None


def load_house_revision_source_context(evidence_root, review_root, *, requested_person_ids,
                                       prior_transactions=(), current_transactions=(), state_status=None):
    """Enumerate fixed-root PTR sources and authenticate context dependencies.

    The current index is the unique latest retrieval per year. Historical keys
    remain visible; disappeared keys are unresolved rather than withdrawals.
    XML-derived identity precedes all PDF/cache selection. Missing/ambiguous
    inputs and opaque pages block this context, while unrelated financial-row
    qualification is outside this component and remains unchanged.
    """
    people = sorted(set(requested_person_ids))
    if len(people) > MAX_REPORTS or any(not isinstance(p, str) or not PERSON.fullmatch(p) for p in people):
        raise ValueError("House revision context person IDs are invalid")
    reasons = set()
    per_person = {p: {"status": "unknown", "reasons": [], "sources": []} for p in people}
    documents = {p: [] for p in people}
    provenance = {"schema_version": VERSION, "status": "unknown", "scope_complete": False,
        "review_projection_proven": False, "reasons": [], "indexed_years": [], "required_report_years": [],
        "scope_years": [], "selected_index_sha_by_year": {}, "roster_sha256": [],
        "ambiguous_sources": [], "source_person_memberships": [], "unindexed_root_source_keys": [],
        "people": per_person, "dependencies": []}
    if not people:
        provenance["status"] = "observed_context"
        return HouseRevisionSourceContext(provenance, documents)
    if evidence_root is None or review_root is None:
        provenance["reasons"] = ["controlled_source_roots_unavailable"]
        return HouseRevisionSourceContext(provenance, documents)
    budget = {"bytes": 0, "paths": set()}
    evidence, review = _Reader(evidence_root, "evidence", budget), _Reader(review_root, "review", budget)
    required_keys = {p: set() for p in people}
    for fact in [*prior_transactions, *current_transactions]:
        if not isinstance(fact, dict) or fact.get("source_id") != "house_clerk" or fact.get("person_id") not in required_keys:
            continue
        url = fact.get("source_url")
        if isinstance(url, str) and url.startswith("https://disclosures-clerk.house.gov/public_disc/financial-pdfs/"):
            continue  # Existing annual disclosures are outside this PTR context.
        match = PTR_URL.fullmatch(url) if isinstance(url, str) else None
        if match is None or str(fact.get("filing_id")) != match[2] or not 2008 <= int(match[1]) <= 2100:
            reasons.add("required_fact_ptr_source_binding_invalid")
        else:
            required_keys[fact["person_id"]].add((int(match[1]), match[2]))
    required_years = {year for keys in required_keys.values() for year, _ in keys}
    provenance["required_report_years"] = sorted(required_years)
    rosters = []
    indexes = {}
    rows_by_key = {}
    all_index_keys = set()
    try:
        roster_paths = [p for p in evidence.paths("house_clerk/members/*.json") if not p.endswith(".roster.json")]
        xml_paths = evidence.paths("house_clerk/members/*.xml")
        if not roster_paths or len(roster_paths) > MAX_ROSTERS:
            raise _Unavailable("actual_roster_missing_or_budget_exceeded")
        if {Path(p).stem for p in roster_paths} != {Path(p).stem for p in xml_paths}:
            raise _Unavailable("actual_roster_sidecar_missing")
        for path in roster_paths:
            digest = Path(path).stem
            if not SHA.fullmatch(digest):
                raise _Unavailable("roster_path_invalid")
            meta, _ = evidence.object(path)
            raw = evidence.read(f"house_clerk/members/{digest}.xml", MAX_MEMBERS_BYTES)
            published, members = parse_members(raw)
            expected = {"source_id": "house_clerk_members", "source_url": MEMBERS_URL,
                "sha256": digest, "archive_path": f"house_clerk/members/{digest}.xml",
                "published_date": published, "record_count": len(members)}
            if _digest(raw) != digest or type(meta.get("record_count")) is not int or any(meta.get(k) != v for k, v in expected.items()):
                raise _Unavailable("roster_metadata_binding_mismatch")
            if "byte_length" in meta and (type(meta["byte_length"]) is not int or meta["byte_length"] != len(raw)):
                raise _Unavailable("roster_metadata_binding_mismatch")
            roster = {"metadata": meta, "members": [asdict(member) for member in members]}
            wrapper = f"house_clerk/members/{digest}.roster.json"
            if evidence.path(wrapper).is_file() and evidence.object(wrapper)[0] != roster:
                raise _Unavailable("roster_cache_differs_from_actual_xml")
            rosters.append(roster)
        provenance["roster_sha256"] = sorted(r["metadata"]["sha256"] for r in rosters)
        index_count = row_count = 0
        directories = []
        for directory in evidence.paths("house_clerk/index/*"):
            if not evidence.path(directory).is_dir():
                continue
            name = Path(directory).name
            if not re.fullmatch(r"[0-9]{4}", name) or not 2008 <= int(name) <= 2100:
                raise _Unavailable("index_year_path_invalid")
            directories.append((int(name), directory))
        provenance["indexed_years"] = sorted(year for year, _ in directories)
        for year, directory in directories:
            versions = []
            for path in evidence.paths(f"{directory}/*.json"):
                index_count += 1
                if index_count > MAX_INDEX_VERSIONS:
                    raise _Unavailable("index_version_budget_exceeded")
                digest = Path(path).stem
                if not SHA.fullmatch(digest):
                    raise _Unavailable("index_path_invalid")
                meta, _ = evidence.object(path)
                raw = evidence.read(f"{directory}/{digest}.zip", MAX_ZIP_BYTES)
                rows = parse_index(raw, year)
                row_count += len(rows)
                if row_count > MAX_INDEX_ROWS:
                    raise _Unavailable("index_row_budget_exceeded")
                expected = {"source_id": "house_clerk", "source_url": INDEX_URL.format(year=year),
                    "filing_year": year, "sha256": digest, "record_count": len(rows),
                    "archive_path": f"{directory}/{digest}.zip"}
                if (_digest(raw) != digest or type(meta.get("record_count")) is not int
                        or any(meta.get(k) != v for k, v in expected.items())):
                    raise _Unavailable("index_metadata_binding_mismatch")
                if "byte_length" in meta and (type(meta["byte_length"]) is not int or meta["byte_length"] != len(raw)):
                    raise _Unavailable("index_metadata_binding_mismatch")
                versions.append((_time(meta.get("retrieved_at")), digest, rows))
            if not versions:
                reasons.add(f"index_year_empty:{year}")
                continue
            versions.sort(key=lambda v: (v[0], v[1]))
            latest = [v for v in versions if v[0] == versions[-1][0]]
            current = {r.document_id for r in latest[0][2] if r.filing_type == "P"} if len(latest) == 1 else set()
            if len(latest) != 1:
                reasons.add(f"index_latest_retrieval_ambiguous:{year}")
            else:
                indexes[str(year)] = latest[0][1]
            for _, digest, rows in versions:
                for row in rows:
                    all_index_keys.add((year, row.document_id))
                    if row.filing_type == "P":
                        item = rows_by_key.setdefault((year, row.document_id), {"current": row.document_id in current, "versions": {}})
                        item["versions"][digest] = asdict(row)
        # Known archive/review originals must not vanish merely because the
        # selected indexes or review cache no longer mention them. Official
        # non-P index keys are explained outside this PTR scope; unindexed
        # keys have no independent person/type exclusion and remain unknown.
        orphan_keys = set()
        for reader, patterns in ((evidence, ("house_clerk/documents/*/*/*.json", "house_clerk/documents/*/*/*.pdf")),
                                 (review, ("house_clerk/extractions/*/*/*.json", "house_clerk/qualifications/*/*/*.json"))):
            for pattern in patterns:
                for path in reader.paths(pattern):
                    parts = path.split("/")
                    if (len(parts) != 5 or not re.fullmatch(r"[0-9]{4}", parts[2])
                            or not re.fullmatch(r"[0-9]{1,20}", parts[3]) or not SHA.fullmatch(Path(path).stem)):
                        raise _Unavailable("root_source_artifact_path_invalid")
                    key = (int(parts[2]), parts[3])
                    if key not in all_index_keys:
                        orphan_keys.add(key)
        provenance["unindexed_root_source_keys"] = [f"{year}/{doc}" for year, doc in sorted(orphan_keys)]
        if orphan_keys:
            reasons.add("root_source_not_bound_to_any_archived_index")
    except (OSError, ValueError, RuntimeError, _Unavailable) as error:
        reasons.add(str(error) if isinstance(error, _Unavailable) else "source_catalog_or_roster_invalid")
    provenance["indexed_years"].sort()
    scope_years = sorted(set(provenance["indexed_years"]) | required_years)
    provenance["scope_years"] = scope_years
    provenance["selected_index_sha_by_year"] = indexes
    for year in scope_years:
        if str(year) not in indexes:
            reasons.add(f"required_year_index_not_selected:{year}")
    if not scope_years:
        reasons.add("source_index_scope_unavailable")
    if state_status is not None:
        if (not isinstance(state_status, dict) or type(state_status.get("filing_year")) is not int
                or not isinstance(state_status.get("index_sha256"), str)
                or indexes.get(str(state_status["filing_year"])) != state_status["index_sha256"]):
            reasons.add("state_index_selection_conflicts_with_latest_source")
    identity_cache = {}
    evaluations = reports = ambiguous_memberships = 0
    for key, item in sorted(rows_by_key.items()):
        year, docid = key
        key_text = f"{year}/{docid}"
        matches = []
        variant_unknown = False
        try:
            identity_variants = {(r["filer_name"], r["state_district"]): r for r in item["versions"].values()}
            for row in identity_variants.values():
                identity_key = (row["filer_name"], row["state_district"])
                if identity_key not in identity_cache:
                    derived = []
                    for roster in rosters:
                        evaluations += 1
                        if evaluations > MAX_IDENTITY_EVALUATIONS:
                            raise _Unavailable("identity_evaluation_budget_exceeded")
                        identity = suggest_identity({"source": row}, roster)
                        if identity.get("status") == "matched_automatically":
                            derived.append({k: v for k, v in identity.items() if k != "document_id"})
                    identity_cache[identity_key] = derived
                if len({i["person_id"] for i in identity_cache[identity_key]}) != 1:
                    variant_unknown = True
                matches.extend({**identity, "document_id": docid} for identity in identity_cache[identity_key])
        except _Unavailable as error:
            reasons.add(str(error))
            break
        found = {identity["person_id"] for identity in matches}
        if len(found) != 1 or variant_unknown:
            ambiguous_memberships += max(1, len(found))
            if ambiguous_memberships > MAX_IDENTITY_EVALUATIONS:
                reasons.add("identity_membership_inventory_budget_exceeded")
                break
            provenance["ambiguous_sources"].append({"key": key_text, "matched_person_ids": sorted(found)})
            reasons.add(f"index_person_membership_unknown:{key_text}")
            continue
        person = next(iter(found))
        provenance["source_person_memberships"].append({"key": key_text, "person_id": person})
        if person not in per_person:
            continue
        reports += 1
        record = {"key": key_text, "current_index_key": item["current"],
            "index_sha256": sorted(item["versions"]), "identity_roster_sha256": sorted({i["roster_sha256"] for i in matches}), "reasons": []}
        per_person[person]["sources"].append(record)
        local = record["reasons"]
        if reports > MAX_REPORTS:
            local.append("source_report_budget_exceeded")
            continue
        if not item["current"]:
            local.append("historical_only_index_key_not_disposed")
        variants = {json.dumps(row, sort_keys=True) for row in item["versions"].values()}
        if len(variants) != 1:
            local.append("official_index_row_version_conflict")
            continue
        row = next(iter(item["versions"].values()))
        if row["filed_date"] is None:
            local.append("official_filing_date_unavailable")
        folder = f"house_clerk/documents/{year}/{docid}"
        try:
            paths = evidence.paths(f"{folder}/*.json")
            pdf_paths = evidence.paths(f"{folder}/*.pdf")
            if len(paths) != 1 or len(pdf_paths) != 1 or Path(paths[0]).stem != Path(pdf_paths[0]).stem:
                raise _Unavailable("source_archive_missing_or_multiple_versions")
            digest = Path(paths[0]).stem
            if not SHA.fullmatch(digest):
                raise _Unavailable("source_archive_path_invalid")
            meta, meta_bytes = evidence.object(paths[0])
            raw = evidence.read(f"{folder}/{digest}.pdf", MAX_PDF_BYTES)
            expected = {"source_id": "house_clerk", "filing_year": year, "document_id": docid,
                "filing_type": "P", "filer_name": row["filer_name"], "state_district": row["state_district"],
                "filed_date": row["filed_date"], "source_url": document_url(year, "P", docid),
                "sha256": digest, "archive_path": f"{folder}/{digest}.pdf", "byte_length": len(raw)}
            if (_digest(raw) != digest or type(meta.get("byte_length")) is not int
                    or any(meta.get(k) != v for k, v in expected.items())):
                raise _Unavailable("canonical_source_metadata_binding_mismatch")
            record["source_sha256"] = digest
            ocr, page_count = _native_plan(raw, digest)
            record["actual_planned_ocr_pages"] = ocr
            record["page_count"] = page_count
            if ocr:
                local.append("opaque_source_cannot_prove_competitor_absence")
            from .house_native_contrast import plan_house_native_contrast
            plan = plan_house_native_contrast(evidence.root, evidence.path(paths[0]))
            record["source_parser_version"] = plan.get("parser_version")
            record["native_contrast_activated"] = plan.get("activated")
            if plan.get("activated") is None:
                local.append("current_source_parser_plan_unavailable")
            cache = {}
            for kind in ("extractions", "qualifications"):
                path = f"house_clerk/{kind}/{year}/{docid}/{digest}.json"
                try:
                    cache[kind] = review.object(path)
                except _Unavailable as error:
                    local.append(f"{kind}:{error}")
            extraction, extraction_bytes = cache.get("extractions", (None, None))
            qualification, qualification_bytes = cache.get("qualifications", (None, None))
            if extraction is not None:
                from .house_ptr import SCHEMA as EXTRACTION_SCHEMA
                source = extraction.get("source")
                source_fields = ("source_id", "source_url", "document_id", "filer_name", "state_district", "filing_year", "filed_date", "archive_path")
                if (extraction.get("schema_version") != EXTRACTION_SCHEMA
                        or extraction.get("source_sha256") != digest or not isinstance(source, dict)
                        or any(source.get(field) != meta.get(field) for field in source_fields)):
                    local.append("extraction_original_source_binding_mismatch")
                if plan.get("activated") is not False and plan.get("parser_version") != extraction.get("parser_version"):
                    local.append("extraction_current_source_parser_version_mismatch")
                if not ocr and extraction.get("extraction_method") != "native_pdf_text":
                    local.append("extraction_method_not_actual_native_source")
                rows = extraction.get("transactions")
                if not isinstance(rows, list):
                    local.append("extraction_transaction_inventory_unavailable")
                elif any(not isinstance(r, dict) or not isinstance(r.get("reported_transaction_id"), str)
                        or not re.fullmatch(r"[0-9]{1,30}", r["reported_transaction_id"]) for r in rows):
                    local.append("source_row_official_id_unavailable")
            if qualification is not None:
                from .house_ptr import QUALIFICATION_SCHEMA
                if (qualification.get("schema_version") != QUALIFICATION_SCHEMA
                        or qualification.get("source_sha256") != digest or qualification.get("document_id") != docid
                        or qualification.get("identity") not in matches):
                    local.append("qualification_source_identity_binding_mismatch")
                if extraction is not None and qualification.get("parser_version") != extraction.get("parser_version"):
                    local.append("qualification_parser_version_mismatch")
            # Pin immutable source/cache snapshots. Any later profile must use
            # these original bytes or independently verify an identical hash.
            documents[person].append(HouseRevisionSourceDocument(year, docid, digest, meta_bytes, raw,
                extraction_bytes, qualification_bytes, tuple(ocr)))
            evidence.read(paths[0], MAX_JSON_BYTES)
            evidence.read(f"{folder}/{digest}.pdf", MAX_PDF_BYTES)
        except (OSError, ValueError, RuntimeError, _Unavailable) as error:
            local.append(str(error) if isinstance(error, _Unavailable) else "source_native_inventory_unavailable")
        local[:] = sorted(set(local))
    for person in people:
        observed_keys = {tuple([int(r["key"].split("/")[0]), r["key"].split("/")[1]]) for r in per_person[person]["sources"]}
        for year, docid in sorted(required_keys[person] - observed_keys):
            per_person[person]["reasons"].append(f"required_fact_source_not_bound_to_index_person:{year}/{docid}")
        if not per_person[person]["sources"]:
            per_person[person]["reasons"].append("no_source_bound_ptrs_for_requested_person")
        per_person[person]["status"] = ("unknown" if reasons or per_person[person]["reasons"]
            or any(r["reasons"] for r in per_person[person]["sources"]) else "observed_context")
    provenance["reasons"] = sorted(reasons)
    provenance["status"] = "observed_context" if all(v["status"] == "observed_context" for v in per_person.values()) else "unknown"
    provenance["dependencies"] = sorted([*evidence.dependencies.values(), *review.dependencies.values()], key=lambda d: (d["root"], d["path"]))
    return HouseRevisionSourceContext(provenance, documents)
