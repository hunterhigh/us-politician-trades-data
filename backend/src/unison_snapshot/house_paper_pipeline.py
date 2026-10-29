"""Bounded, source-bound House paper observation inside the review workflow.

The legacy extraction remains the comparison artifact. Existing qualified
facts are retained unless complete historical and original-source replay
permits a same-ID owner correction. Saved interpretation never starts OCR.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .codec import encode
from .house_paper_commit import (paper_review_access, commit_paper_materialization,
    recover_paper_materialization, recover_pending_paper_materializations)
from .house_ptr_amount_header import observe_amount_header
from .house_ptr_amount_header_preparation import prepare_amount_headers
from .house_ptr_cell_observation import observe_house_ptr_pdf
from .house_asset_line_order import MODE as ASSET_LINE_MODE
from .house_ptr_key_field_reread import (
    MATERIALIZATION_SELECTION, missing_key_field_rereads, plan_key_field_rereads,
    reread_doubtful_key_cells)
from .house_ptr_paper_qualification import qualify_paper_observation, materialize_paper_observation
from .house_ptr_paper_temporal import (PARSER_VERSION, audit_paper_temporal_relations,
                                      validate_paper_source_metadata)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(encode(value))
    temporary.replace(path)


def preserved_paper_materialization(metadata: dict, extraction: dict,
                                   qualification: dict, observation: dict) -> bool:
    """Keep a bound saved result out of legacy retries, retaining old failures.

    A method label alone cannot protect an arbitrary artifact. Incomplete or
    contradictory saved results stop selection instead of destructive fallback.
    """
    if extraction.get("extraction_method") != "source_bound_paper_cells":
        return False
    from .house_ptr import SCHEMA, QUALIFICATION_SCHEMA
    from .house_ptr_paper_qualification import FACT_PARSER_VERSION, paper_materialization_counts
    document_id, source_sha, _, _ = validate_paper_source_metadata(metadata)
    receipt = qualification.get("observation_materialization") or {}
    digest = hashlib.sha256(json.dumps(observation, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if (extraction.get("schema_version") != SCHEMA or
            qualification.get("schema_version") != QUALIFICATION_SCHEMA or
            extraction.get("source") != metadata or extraction.get("source_sha256") != source_sha or
            qualification.get("source_sha256") != source_sha or qualification.get("document_id") != document_id or
            extraction.get("parser_version") != FACT_PARSER_VERSION or
            qualification.get("parser_version") != FACT_PARSER_VERSION or
            observation.get("source_sha256") != source_sha or receipt.get("observation_sha256") != digest or
            paper_materialization_counts(qualification, extraction) is None):
        raise ValueError("House saved paper retry protection has invalid source/qualification binding")
    return True


def _same_filer_peers(evidence_root: Path, target: dict) -> list[dict]:
    """Use only source-bound PTR catalog entries for chronology hints."""
    peers = []
    for path in sorted((evidence_root / "house_clerk" / "documents").glob("*/*/*.json")):
        value = _read(path)
        if (value.get("document_id") == target["document_id"] or
                value.get("filing_type") != "P" or
                value.get("filer_name") != target["filer_name"] or
                value.get("state_district") != target["state_district"]):
            continue
        validate_paper_source_metadata(value)
        peers.append(value)
    return peers


def bridge_paper_report(*, evidence_root: Path, review_root: Path,
                        metadata_path: Path, tesseract: str,
                        observe_new: bool, materialize_saved: bool = False) -> str:
    """Bridge one report, recovering any prepared result before interpretation."""
    with paper_review_access(review_root):
        return _bridge_paper_report(evidence_root=evidence_root,review_root=review_root,
            metadata_path=metadata_path,tesseract=tesseract,observe_new=observe_new,
            materialize_saved=materialize_saved)


def _bridge_paper_report(*, evidence_root: Path, review_root: Path,
                         metadata_path: Path, tesseract: str,
                         observe_new: bool, materialize_saved: bool = False) -> str:
    """Return the review disposition; saved fact interpretation is explicit opt-in."""
    evidence_root = evidence_root.resolve()
    review_root = review_root.resolve()
    metadata_path = metadata_path.resolve()
    if evidence_root not in metadata_path.parents:
        raise ValueError("House paper metadata escapes evidence root")
    metadata = _read(metadata_path)
    document_id, source_sha, _, _ = validate_paper_source_metadata(metadata)
    if recover_paper_materialization(review_root,evidence_root,metadata):
        return "recovered_saved_materialization"
    relative = Path(str(metadata["filing_year"])) / document_id / f"{source_sha}.json"
    extraction_path = review_root / "house_clerk" / "extractions" / relative
    identity_path = review_root / "house_clerk" / "identities" / relative
    qualification_path = review_root / "house_clerk" / "qualifications" / relative
    observation_path = review_root / "house_clerk" / "paper_observations" / relative
    header_path = review_root / "house_clerk" / "paper_amount_headers" / relative
    reread_path = review_root / "house_clerk" / "paper_key_field_rereads" / relative
    reread_failure_path = review_root / "house_clerk" / "paper_reread_failures" / relative
    temporal_failure_path = review_root / "house_clerk" / "paper_temporal_failures" / relative
    failed_legacy_path = review_root / "house_clerk" / "failures" / relative
    legacy = _read(qualification_path) if qualification_path.is_file() else None
    extraction = _read(extraction_path) if extraction_path.is_file() else None
    if not all(path.is_file() for path in (extraction_path, identity_path, qualification_path)):
        if not (materialize_saved and identity_path.is_file() and failed_legacy_path.is_file()
                and observation_path.is_file() and header_path.is_file()):
            return "missing_legacy_inputs"
        failure = _read(failed_legacy_path)
        if (failure.get("source_sha256") != source_sha or failure.get("document_id") != document_id
                or failure.get("schema_version") != "house-ptr-parse-failure/v1"):
            raise ValueError("House saved materialization failure source mismatch")
    if extraction is not None and extraction.get("extraction_method") != "tesseract_legacy_checkbox":
        if not (materialize_saved and extraction.get("extraction_method") == "source_bound_paper_cells"):
            return "not_legacy_paper"
    if legacy is not None:
        if legacy.get("source_sha256") != source_sha or legacy.get("document_id") != document_id:
            raise ValueError("House legacy qualification does not match paper source")
        if not isinstance(legacy.get("transactions"), list):
            raise ValueError("House legacy qualification has no transaction list")
        if legacy["transactions"]:
            if not (materialize_saved and all(path.is_file() for path in (
                    extraction_path, identity_path, observation_path, header_path, reread_path))):
                return "legacy_qualified_preserved"
    if observation_path.is_file() and header_path.is_file():
        observation = _read(observation_path)
        header = _read(header_path)
        disposition = "requalified_existing_observation"
    elif observe_new:
        pdf_path = (evidence_root / metadata["archive_path"]).resolve()
        if evidence_root not in pdf_path.parents:
            raise ValueError("House paper archive path escapes evidence root")
        pdf = pdf_path.read_bytes()
        if hashlib.sha256(pdf).hexdigest() != source_sha:
            raise ValueError("House paper archive hash mismatch")
        observation = observe_house_ptr_pdf(pdf, source_sha256=source_sha,
                                            executable=tesseract, asset_line_mode=ASSET_LINE_MODE)
        header = observe_amount_header(pdf, source_sha256=source_sha,
                                       executable=tesseract)
        disposition = "observed_and_qualified"
    else:
        return "not_selected"
    if observation.get("source_sha256") != source_sha or header.get("source_sha256") != source_sha:
        raise ValueError("House paper observation source mismatch")
    if observe_new and not materialize_saved and header.get("status") == "header_unresolved":
        pdf_path = (evidence_root / metadata["archive_path"]).resolve()
        if evidence_root not in pdf_path.parents:
            raise ValueError("House paper amount preparation archive escapes evidence root")
        header = prepare_amount_headers(pdf_path.read_bytes(), observation,
            executable=tesseract, existing_header=header)
    identity = _read(identity_path)
    paper = qualify_paper_observation(observation, metadata, identity,
                                      amount_header=header)
    if paper["parser_version"] != PARSER_VERSION or paper["transactions"]:
        raise ValueError("House paper bridge produced an ineligible qualification")
    try:
        peers = _same_filer_peers(evidence_root, metadata)
        if peers and any(isinstance(row.get("raw_fields"), dict) and
                         row["raw_fields"].get("event_date") is not None
                         for row in paper.get("quarantined", [])):
            paper["temporal_relation_observation"] = audit_paper_temporal_relations(
                paper, metadata, peers)
        temporal_failure_path.unlink(missing_ok=True)
    except Exception as error:
        _write(temporal_failure_path, {
            "schema_version": "house-paper-temporal-failure/v1",
            "document_id": document_id, "source_sha256": source_sha,
            "error": f"{type(error).__name__}: {str(error)[:320]}",
        })
    reread = None
    if (any(row.get("source_quality_flags") for row in paper.get("quarantined", [])) or
            any(row.get("disposition") == "data_candidate" and row.get("cells") is not None
                for page in observation["pages"] for row in page.get("rows", []))):
        missing = []
        try:
            plan = plan_key_field_rereads(observation)
            reread = _read(reread_path) if reread_path.is_file() else None
            try:
                missing = missing_key_field_rereads(plan, reread)
            except ValueError:
                # Wrong-source, malformed and obsolete caches are not input
                # to either a new preparation or saved materialization.
                reread = None
                missing = list(plan["requests"])
            if missing:
                if materialize_saved:
                    raise ValueError(f"House saved materialization lacks {len(missing)} required secondary readings; OCR not started")
                pdf_path = (evidence_root / metadata["archive_path"]).resolve()
                if evidence_root not in pdf_path.parents:
                    raise ValueError("House paper reread archive path escapes evidence root")
                reread = reread_doubtful_key_cells(
                    pdf_path.read_bytes(), observation, executable=Path(tesseract),
                    selection_mode=MATERIALIZATION_SELECTION, existing_reread=reread)
            if missing_key_field_rereads(plan, reread):
                raise ValueError("House paper secondary preparation is incomplete")
            paper["key_field_reread_status"] = "observed"
            if reread is not None:
                _write(reread_path, reread)
            reread_failure_path.unlink(missing_ok=True)
        except Exception as error:
            paper["key_field_reread_status"] = "unresolved"
            _write(reread_failure_path, {
                "schema_version": "house-paper-reread-failure/v1",
                "document_id": document_id, "source_sha256": source_sha,
                "missing_required_cells": [{k: item[k] for k in ("page", "physical_band_index", "field")}
                                           for item in missing],
                "error": f"{type(error).__name__}: {str(error)[:320]}",
            })
    if materialize_saved:
        pdf_path = (evidence_root / metadata["archive_path"]).resolve()
        if evidence_root not in pdf_path.parents:
            raise ValueError("House paper materialization archive escapes evidence root")
        if reread is None and reread_path.is_file():
            cached = _read(reread_path)
            # Saved replay may consume a valid partial cache, keeping missing
            # cells quarantined. It never revives a wrong-source cache.
            try:
                missing_key_field_rereads(plan_key_field_rereads(observation), cached)
                reread = cached
            except ValueError:
                pass
        paper = materialize_paper_observation(pdf_path.read_bytes(), observation, metadata,
            identity, amount_header=header, field_reread=reread, prior_qualification=legacy,
            prior_extraction=extraction)
        if legacy is not None and legacy.get("transactions") and paper == legacy:
            return "legacy_qualified_preserved"
        disposition = "materialized_existing_observation"
        interpreted = paper.get("materialized_extraction")
        if (not isinstance(interpreted, dict) or interpreted.get("source_sha256") != source_sha or
                interpreted.get("source", {}).get("document_id") != document_id or
                interpreted.get("extraction_method") != "source_bound_paper_cells" or
                not preserved_paper_materialization(metadata, interpreted, paper, observation)):
            raise ValueError("House materialized extraction source mismatch")
        outputs={"observation":observation,"header":header,"extraction":interpreted,"qualification":paper}
        reference = legacy.get("legacy_extraction_reference") if legacy else None
        if reference is not None:
            reference = dict(reference)
            old_path = (review_root / reference.get("path", "")).resolve()
            if review_root not in old_path.parents:
                raise ValueError("House legacy extraction reference escapes review")
            saved = _read(old_path)
            digest = hashlib.sha256(encode(saved.get("extraction"))).hexdigest()
            if (saved.get("schema_version") != "house-paper-legacy-extraction-reference/v1" or
                    saved.get("source_sha256") != source_sha or saved.get("document_id") != document_id or
                    reference != {"path": old_path.relative_to(review_root).as_posix(),
                        "source_sha256": source_sha, "document_id": document_id,
                        "canonical_extraction_sha256": digest} or
                    saved.get("canonical_extraction_sha256") != digest):
                raise ValueError("House legacy extraction reference binding differs")
            outputs["legacy_reference"]=saved
        if extraction is not None and extraction.get("extraction_method") == "tesseract_legacy_checkbox":
            digest = hashlib.sha256(encode(extraction)).hexdigest()
            from .house_paper_owner_correction import PROFILE_KEY, _legacy_reference
            if PROFILE_KEY in paper:
                reference = _legacy_reference(extraction, metadata)
                old_path = (review_root / reference["path"]).resolve()
            else:
                old_path = (review_root / "house_clerk/paper_legacy_extraction_references" /
                            relative.with_suffix("") / (digest + ".json"))
                reference = {"path": old_path.relative_to(review_root).as_posix(),
                    "source_sha256": source_sha, "document_id": document_id,
                    "canonical_extraction_sha256": digest}
            saved = {"schema_version": "house-paper-legacy-extraction-reference/v1",
                     **{key: value for key, value in reference.items() if key != "path"},
                     "extraction": extraction}
            if old_path.is_file() and _read(old_path) != saved:
                raise ValueError("House legacy extraction reference already differs")
            outputs["legacy_reference"]=saved
        if reference is not None:
            paper["legacy_extraction_reference"] = reference
        commit_paper_materialization(review_root,evidence_root,metadata,outputs)
        return disposition
    # Write reusable source observations first; the qualification is replaced
    # only after the complete paper result has been constructed and checked.
    _write(observation_path, observation)
    _write(header_path, header)
    _write(qualification_path, paper)
    return disposition


def bridge_paper_batch(*, evidence_root: Path, review_root: Path,
                       parse_batch: Path, tesseract: str,
                       document_id: str | None = None, max_new: int = 2,
                       materialize_saved: bool = False) -> dict:
    """Replay saved observations and observe a bounded set of newly selected PDFs."""
    evidence_root = evidence_root.resolve()
    review_root = review_root.resolve()
    if type(max_new) is not int or not 1 <= max_new <= 2:
        raise ValueError("House paper batch size is outside bounds")
    if document_id is not None and (not document_id.isdecimal() or
                                    not 1 <= len(document_id) <= 12):
        raise ValueError("House paper document ID is invalid")
    metadata_root = evidence_root / "house_clerk" / "documents"
    observation_root = review_root / "house_clerk" / "paper_observations"
    replay = [metadata_root / path.relative_to(observation_root)
              for path in sorted(observation_root.glob("*/*/*.json"))]
    selected = [Path(line) for line in parse_batch.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    explicit_target = None
    if document_id is not None:
        matches = list(metadata_root.glob(f"*/{document_id}/*.json"))
        if len(matches) != 1:
            raise ValueError("House paper document ID has no unique archived source")
        selected.insert(0, matches[0])
        explicit_target = matches[0].resolve()
    recovered=recover_pending_paper_materializations(evidence_root,review_root)
    outcomes: dict[str, int] = {"recovered_saved_materialization":len(recovered)} if recovered else {}
    seen: set[Path] = set(recovered)
    new_count = 0
    for path in [*replay, *selected]:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file() or metadata_root.resolve() not in path.parents:
            raise ValueError("House paper batch metadata is missing or outside evidence")
        is_new = path not in replay
        if is_new and new_count >= max_new:
            continue
        metadata = _read(path)
        relative = path.relative_to(metadata_root.resolve())
        failure_path = review_root / "house_clerk" / "paper_failures" / relative
        try:
            disposition = bridge_paper_report(
                evidence_root=evidence_root, review_root=review_root,
                metadata_path=path, tesseract=tesseract,
                observe_new=(is_new or path == explicit_target) and not materialize_saved,
                materialize_saved=materialize_saved)
        except Exception as error:
            disposition = "paper_observation_failed"
            _write(failure_path, {
                "schema_version": "house-paper-observation-failure/v1",
                "document_id": metadata.get("document_id"),
                "source_sha256": metadata.get("sha256"),
                "parser_version": PARSER_VERSION,
                "error": f"{type(error).__name__}: {str(error)[:320]}",
            })
        else:
            if disposition in {"observed_and_qualified", "requalified_existing_observation", "materialized_existing_observation"}:
                failure_path.unlink(missing_ok=True)
        if disposition == "observed_and_qualified" or disposition == "paper_observation_failed":
            new_count += int(is_new)
        outcomes[disposition] = outcomes.get(disposition, 0) + 1
    return dict(sorted(outcomes.items()))
