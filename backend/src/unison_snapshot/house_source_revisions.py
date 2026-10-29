"""Use the original source scope before applying cached House revision chains."""
from __future__ import annotations

from copy import deepcopy
import re


def _ptr(row):
    return (row.get("source_id") == "house_clerk"
            and re.fullmatch(r"https://disclosures-clerk\.house\.gov/public_disc/ptr-pdfs/[0-9]{4}/[0-9]{1,20}\.pdf",
                             str(row.get("source_url", ""))) is not None)


def resolve_source_bound_house_revisions(inputs, current_transactions, *,
        prior_transactions=None, evidence_root=None, review_root=None, state_status=None):
    """Keep normal candidate/status revision semantics on one source guard.

    The legacy resolver is a useful deterministic chain algorithm, but complete
    replay of saved words cannot establish the universe of possible sources.
    Never allow its proposed destructive changes to escape for an unproven
    person. Other people and ordinary independently qualified rows are retained.
    """
    from .house_ptr import resolve_automatic_revisions
    if not any(q.get("revision_observations") for q, _ in inputs):
        return resolve_automatic_revisions(inputs, current_transactions,
            prior_transactions=prior_transactions, evidence_root=evidence_root)
    from .house_revision_scope_guard import derive_house_revision_scope
    guard = derive_house_revision_scope(inputs, current_transactions,
        prior_transactions=prior_transactions or [], evidence_root=evidence_root,
        review_root=review_root, state_status=state_status)
    allowed = {tuple(group) for group in guard["allowed_groups"]}
    people = {q.get("identity", {}).get("person_id") for q, _ in inputs
              if q.get("revision_observations")}
    denied = {person for person in people if not any(group[0] == person for group in allowed)}
    # Passing only the proved people to the chain algorithm avoids computing a
    # speculative promotion and trying to undo its qualification side effects.
    selected = [(q, x) for q, x in inputs if q.get("identity", {}).get("person_id") not in denied]
    result = resolve_automatic_revisions(selected, current_transactions,
        prior_transactions=prior_transactions, evidence_root=evidence_root)
    if any((mapping["person_id"], mapping["reported_transaction_id"]) not in allowed
           for mapping in result["replacements"]):
        raise ValueError("House revision chain escapes the original-bound ID scope")
    if denied:
        facts = {row["id"]: deepcopy(row) for row in result["transactions"]}
        from .house_ptr import qualify_automatic, revision_reference_dispositions
        from .house_native_contrast import PARSER_VERSIONS as CONTRAST_VERSIONS
        from .house_ptr_paper_qualification import paper_materialization_counts
        pending = {}
        prior_ids = {row["id"] for row in prior_transactions or []}
        for qualification, extraction in inputs:
            person = qualification.get("identity", {}).get("person_id")
            if person not in denied:
                continue
            document = str(qualification.get("document_id"))
            paper = paper_materialization_counts(qualification) is not None
            effective_source = qualification.get('materialized_extraction') if paper else extraction
            effective_source = effective_source if isinstance(effective_source, dict) else extraction
            amended_rows = [row for row in effective_source.get('transactions', [])
                            if str(row.get('filing_status', '')).lower() == 'amended']
            # A cache can contain an earlier speculative promotion. The
            # default qualifier, without revision permissions, is the only
            # pending interpretation returned to the normal status consumer.
            # Legacy ordinary-row interpretation retains its existing cache
            # boundary. It supplies no original-scope permission. The source-
            # bound contrast variants still require their original replay.
            if qualification.get('revision_observations') or (not paper and amended_rows):
                roots = {'evidence_root': evidence_root} if extraction.get('parser_version') in CONTRAST_VERSIONS else {}
                pending[document] = qualify_automatic(extraction, qualification.get("identity", {}), **roots)
            else:
                # Ordinary materialized paper rows keep their existing source-
                # cell qualification and unknown-slot accounting. A native
                # default replay is not a substitute for the paper consumer.
                pending[document] = deepcopy(qualification)
            for row in amended_rows:
                identifier = row.get("extraction_id")
                if identifier not in prior_ids:
                    facts.pop(identifier, None)
        expected_facts = {row["id"]: row for q in pending.values() for row in q.get("transactions", [])}
        blocked_documents = {(row.get("person_id"), str(row.get("filing_id")))
            for row in prior_transactions or [] if _ptr(row) and row.get("person_id") in denied
            and expected_facts.get(row["id"]) != row}
        facts = {key: row for key, row in facts.items() if not (_ptr(row)
            and (row.get("person_id"), str(row.get("filing_id"))) in blocked_documents)}
        for row in prior_transactions or []:
            if _ptr(row) and row.get("person_id") in denied:
                facts[row["id"]] = deepcopy(row)
        result["transactions"] = [facts[key] for key in sorted(facts)]
        for qualification, extraction in inputs:
            person = qualification.get("identity", {}).get("person_id")
            if person not in denied:
                continue
            document = str(qualification.get("document_id"))
            result["qualifications"][document] = pending[document]
            result["identities"][document] = deepcopy(qualification.get("identity", {}))
            if qualification.get("revision_observations"):
                observations = revision_reference_dispositions(pending[document], extraction, result["transactions"])
                for observation in observations:
                    observation.update(disposition="revision_original_source_scope_unproven",
                                       action="retain_prior")
                    observation.pop("replacement", None)
                result["dispositions"][document] = observations
    result["source_scope_audit"] = guard.audit()
    return result
