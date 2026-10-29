"""Recompute House person/source scope before any destructive revision edge.

Only runtime original readers supply scope observations. Cached qualifications,
saved scope flags, and serialized relationship mappings cannot authorize this
guard. Its allowed groups are a source-scope gate, not a proof of a unique chain.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import io
import json
import re

from .house_revision_source_context import load_house_revision_source_context

VERSION = "house-revision-scope-guard/v1"
MAX_INPUTS = 25_000
MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_AUDIT_BYTES = 64 * 1024 * 1024
PERSON = re.compile(r"house:[A-Z][0-9]{6}")
SHA = re.compile(r"[a-f0-9]{64}")


def _fingerprint(value):
    """Exact JSON key/type/presence comparison with a bounded encoding."""
    digest, size = hashlib.sha256(), 0
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).iterencode(value):
        raw = chunk.encode("utf-8")
        size += len(raw)
        if size > MAX_OBJECT_BYTES:
            raise ValueError("scope input JSON budget exceeded")
        digest.update(raw)
    return size, digest.digest()


class HouseRevisionScopeGuard(Mapping):
    """Read-only diagnostic mapping; audit() returns an independent JSON copy."""
    __slots__ = ("_audit_bytes",)

    def __init__(self, audit):
        output = io.BytesIO()
        failure = None
        try:
            for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False).iterencode(audit):
                raw = chunk.encode("utf-8")
                if output.tell() + len(raw) > MAX_AUDIT_BYTES:
                    failure = "scope_audit_byte_budget_exceeded"
                    break
                output.write(raw)
        except (ValueError, TypeError, RecursionError):
            failure = "scope_audit_not_json"
        if failure is not None:
            audit = {"schema_version": VERSION, "status": "unknown", "relationship_proven": False,
                "allowed_groups": [], "people": {p: {"person_id": p, "status": "unknown",
                    "reasons": [failure], "allowed_official_ids": []} for p in audit.get("people", {})},
                "reasons": [failure], "source_context": None, "source_proofs": []}
            self._audit_bytes = json.dumps(audit, sort_keys=True, separators=(",", ":")).encode()
        else:
            self._audit_bytes = output.getvalue()

    def __getitem__(self, key):
        return self.audit()[key]

    def __iter__(self):
        return iter(self.audit())

    def __len__(self):
        return len(self.audit())

    def audit(self):
        return json.loads(self._audit_bytes)


def _input_key(extraction):
    if not isinstance(extraction, dict) or not isinstance(extraction.get("source"), dict):
        return None
    source = extraction["source"]
    year, doc, digest = source.get("filing_year"), source.get("document_id"), extraction.get("source_sha256")
    if (type(year) is not int or not 2008 <= year <= 2100 or not isinstance(doc, str)
            or not re.fullmatch(r"[0-9]{1,20}", doc) or not isinstance(digest, str) or not SHA.fullmatch(digest)):
        return None
    return year, doc, digest


def derive_house_revision_scope(inputs, current_transactions, *, prior_transactions,
                                  evidence_root, review_root, state_status=None):
    """Bind complete runtime inputs to all original same-person source proofs.

    A pending revision is only a request to inspect a source person. Discovery
    never filters the index by the request's cached official transaction IDs.
    Unknown context, missing/extra selected inputs, failed native scope, or a
    changed qualification blocks *all* destructive groups for that person.
    Independent ordinary financial-row qualification is not changed here.
    """
    audit = {"schema_version": VERSION, "status": "unknown", "relationship_proven": False,
        "allowed_groups": [], "people": {}, "reasons": [], "source_context": None, "source_proofs": []}
    if not isinstance(inputs, (list, tuple)) or len(inputs) > MAX_INPUTS:
        audit["reasons"] = ["source_input_inventory_invalid_or_budget_exceeded"]
        return HouseRevisionScopeGuard(audit)
    requested = set()
    by_key = {}
    global_reasons = set()
    pairs = []
    for pair in inputs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or not all(isinstance(v, dict) for v in pair):
            global_reasons.add("source_input_pair_invalid")
            continue
        qualification, extraction = pair
        pairs.append(pair)
        key = _input_key(extraction)
        if key is not None:
            by_key.setdefault(key, []).append(pair)
        if qualification.get("revision_observations"):
            identity = qualification.get("identity")
            person = identity.get("person_id") if isinstance(identity, dict) else None
            if not isinstance(person, str) or not PERSON.fullmatch(person):
                global_reasons.add("revision_request_person_unavailable")
            else:
                requested.add(person)
    if not requested:
        audit["status"] = "unknown" if global_reasons else "not_requested"
        audit["reasons"] = sorted(global_reasons)
        return HouseRevisionScopeGuard(audit)
    from .house_ptr import qualify_automatic
    from .house_revision_source_scope import VERSION as SOURCE_VERSION, inspect_native_house_revision_source
    try:
        context = load_house_revision_source_context(evidence_root, review_root,
            requested_person_ids=sorted(requested), prior_transactions=prior_transactions,
            current_transactions=current_transactions, state_status=state_status)
    except (OSError, ValueError, RuntimeError) as error:
        audit["reasons"] = ["source_context_reconstruction_failed:" + type(error).__name__]
        return HouseRevisionScopeGuard(audit)
    audit["source_context"] = context.provenance()
    for person in sorted(requested):
        reasons = set(global_reasons)
        record = {"person_id": person, "status": "unknown", "reasons": [], "allowed_official_ids": []}
        audit["people"][person] = record
        person_context = context.for_person(person)
        if person_context is None or person_context["status"] != "observed_context":
            reasons.add("person_source_context_unknown")
            if person_context is not None:
                reasons.update(person_context["reasons"])
                for source in person_context["sources"]:
                    reasons.update(f"{source['key']}:{reason}" for reason in source["reasons"])
            record["reasons"] = sorted(reasons)
            continue
        originals = context.documents_for(person)
        expected_keys = {(d.filing_year, d.document_id, d.source_sha256) for d in originals}
        expected_records = {r["key"]: r for r in person_context["sources"]}
        if len(originals) != len(person_context["sources"]) or len(expected_keys) != len(originals):
            reasons.add("person_original_source_set_not_closed")
        # Extra caller sources for this person must not enter the pure resolver
        # merely because another original proved the same official ID group.
        for qualification, extraction in pairs:
            identity = qualification.get("identity")
            if isinstance(identity, dict) and identity.get("person_id") == person and _input_key(extraction) not in expected_keys:
                reasons.add("caller_person_source_outside_original_context")
        official_ids = set()
        for document in originals:
            key = (document.filing_year, document.document_id, document.source_sha256)
            label = f"{document.filing_year}/{document.document_id}"
            supplied = by_key.get(key, [])
            if len(supplied) != 1:
                reasons.add(f"{label}:selected_source_input_missing_or_duplicate")
                continue
            qualification, extraction = supplied[0]
            saved_q, saved_x = document.qualification, document.extraction
            try:
                if _fingerprint(qualification) != _fingerprint(saved_q) or _fingerprint(extraction) != _fingerprint(saved_x):
                    reasons.add(f"{label}:caller_input_differs_from_selected_original_cache")
                    continue
                # Default qualifier leaves amendment relationship unresolved;
                # only the separate resolver can later grant a unique edge.
                expected_q = qualify_automatic(extraction, saved_q["identity"], evidence_root=evidence_root)
                if _fingerprint(expected_q) != _fingerprint(saved_q):
                    reasons.add(f"{label}:qualification_differs_from_normal_replay")
                    continue
            except (KeyError, TypeError, ValueError, RuntimeError):
                reasons.add(f"{label}:qualification_original_replay_failed")
                continue
            proof = inspect_native_house_revision_source(document)
            audit["source_proofs"].append({"person_id": person, "key": label, "proof": proof})
            if (not isinstance(proof, dict) or proof.get("schema_version") != SOURCE_VERSION
                    or proof.get("status") != "observed_scope" or proof.get("scope_complete") is not True
                    or proof.get("document_id") != document.document_id
                    or proof.get("source_sha256") != document.source_sha256):
                reasons.add(f"{label}:original_native_source_scope_unknown")
                continue
            pages = proof.get("pages")
            expected_count = expected_records[label].get("page_count")
            if (not isinstance(pages, list) or len(pages) != expected_count
                    or any(not isinstance(p, dict) or type(p.get("page_number")) is not int for p in pages)
                    or [p.get("page_number") for p in pages if isinstance(p, dict)] != list(range(1, expected_count + 1))):
                reasons.add(f"{label}:source_scope_page_inventory_mismatch")
                continue
            rows = extraction["transactions"]
            represented = set()
            declarations_valid = True
            for page in pages:
                declarations = page.get("declarations")
                if page.get("status") != "observed" or not isinstance(declarations, list):
                    declarations_valid = False
                    continue
                for declaration in declarations:
                    if not isinstance(declaration, dict):
                        declarations_valid = False
                        continue
                    number = declaration.get("reported_transaction_id")
                    indices = declaration.get("source_row_indices")
                    if (not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,30}", number)
                            or not isinstance(indices, list) or len(indices) != 1 or type(indices[0]) is not int
                            or not 0 <= indices[0] < len(rows) or rows[indices[0]].get("reported_transaction_id") != number):
                        declarations_valid = False
                        continue
                    represented.add(indices[0])
                    official_ids.add(number)
            if not declarations_valid or represented != set(range(len(rows))):
                reasons.add(f"{label}:source_scope_declaration_row_inventory_mismatch")
        record["reasons"] = sorted(reasons)
        if not reasons:
            record["status"] = "observed_scope"
            record["allowed_official_ids"] = sorted(official_ids)
            audit["allowed_groups"].extend([[person, number] for number in sorted(official_ids)])
    audit["reasons"] = sorted(global_reasons)
    audit["status"] = "observed_scope" if all(r["status"] == "observed_scope" for r in audit["people"].values()) else "unknown"
    return HouseRevisionScopeGuard(audit)
