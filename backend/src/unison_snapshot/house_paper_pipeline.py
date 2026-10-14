"""Bounded, source-bound House paper observation inside the review workflow.

The legacy extraction remains the comparison artifact.  A paper qualification
may replace its qualification only when the freshly rebuilt legacy result has
no qualified transactions.  Observation failures leave that result intact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .codec import encode
from .house_ptr_amount_header import observe_amount_header
from .house_ptr_cell_observation_shadow import observe_house_ptr_pdf
from .house_ptr_paper_qualification import qualify_paper_observation
from .house_ptr_paper_temporal import PARSER_VERSION, validate_paper_source_metadata


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_bytes(encode(value))
    temporary.replace(path)


def bridge_paper_report(*, evidence_root: Path, review_root: Path,
                        metadata_path: Path, tesseract: str,
                        observe_new: bool) -> str:
    """Return a disposition; never turn an OCR observation into a formal row."""
    evidence_root = evidence_root.resolve()
    review_root = review_root.resolve()
    metadata_path = metadata_path.resolve()
    if evidence_root not in metadata_path.parents:
        raise ValueError("House paper metadata escapes evidence root")
    metadata = _read(metadata_path)
    document_id, source_sha, _, _ = validate_paper_source_metadata(metadata)
    relative = Path(str(metadata["filing_year"])) / document_id / f"{source_sha}.json"
    extraction_path = review_root / "house_clerk" / "extractions" / relative
    identity_path = review_root / "house_clerk" / "identities" / relative
    qualification_path = review_root / "house_clerk" / "qualifications" / relative
    observation_path = review_root / "house_clerk" / "paper_observations" / relative
    header_path = review_root / "house_clerk" / "paper_amount_headers" / relative
    if not all(path.is_file() for path in (extraction_path, identity_path,
                                            qualification_path)):
        return "missing_legacy_inputs"
    extraction = _read(extraction_path)
    legacy = _read(qualification_path)
    if extraction.get("extraction_method") != "tesseract_legacy_checkbox":
        return "not_legacy_paper"
    if legacy.get("source_sha256") != source_sha or legacy.get("document_id") != document_id:
        raise ValueError("House legacy qualification does not match paper source")
    if not isinstance(legacy.get("transactions"), list):
        raise ValueError("House legacy qualification has no transaction list")
    if legacy["transactions"]:
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
                                            executable=tesseract)
        header = observe_amount_header(pdf, source_sha256=source_sha,
                                       executable=tesseract)
        disposition = "observed_and_qualified"
    else:
        return "not_selected"
    if observation.get("source_sha256") != source_sha or header.get("source_sha256") != source_sha:
        raise ValueError("House paper observation source mismatch")
    identity = _read(identity_path)
    paper = qualify_paper_observation(observation, metadata, identity,
                                      amount_header=header)
    if paper["parser_version"] != PARSER_VERSION or paper["transactions"]:
        raise ValueError("House paper bridge produced an ineligible qualification")
    # Write reusable source observations first; the qualification is replaced
    # only after the complete paper result has been constructed and checked.
    _write(observation_path, observation)
    _write(header_path, header)
    _write(qualification_path, paper)
    return disposition


def bridge_paper_batch(*, evidence_root: Path, review_root: Path,
                       parse_batch: Path, tesseract: str,
                       document_id: str | None = None, max_new: int = 2) -> dict:
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
    if document_id is not None:
        matches = list(metadata_root.glob(f"*/{document_id}/*.json"))
        if len(matches) != 1:
            raise ValueError("House paper document ID has no unique archived source")
        selected.insert(0, matches[0])
    outcomes: dict[str, int] = {}
    seen: set[Path] = set()
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
                metadata_path=path, tesseract=tesseract, observe_new=is_new)
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
            if disposition in {"observed_and_qualified", "requalified_existing_observation"}:
                failure_path.unlink(missing_ok=True)
        if disposition == "observed_and_qualified" or disposition == "paper_observation_failed":
            new_count += int(is_new)
        outcomes[disposition] = outcomes.get(disposition, 0) + 1
    return dict(sorted(outcomes.items()))
