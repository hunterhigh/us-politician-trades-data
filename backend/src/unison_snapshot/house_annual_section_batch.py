"""Bounded sidecar batch for House annual sections, isolated from holdings."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .house_annual_sections import SCHEMA, observe_archived_annual_pdf


OBSERVATIONS = Path("house_clerk/holding_section_observations")
FAILURES = Path("house_clerk/holding_section_failures")
EXTRACTIONS = Path("house_clerk/holding_extractions")
QUALIFICATIONS = Path("house_clerk/holding_qualifications")


def _relative(metadata: dict) -> Path:
    if (not re.fullmatch(r"20\d{2}", str(metadata.get("filing_year"))) or
            not re.fullmatch(r"\d{1,12}", str(metadata.get("document_id"))) or
            not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("sha256")))):
        raise ValueError("House annual section source identity is invalid")
    return (Path(str(metadata["filing_year"])) / str(metadata["document_id"]) /
            f"{metadata['sha256']}.json")


def select_section_batch(evidence_root: Path, review_root: Path,
                         newly_parsed_metadata: list[Path], *, old_limit: int = 2) -> list[Path]:
    if not 0 <= old_limit <= 2:
        raise ValueError("House annual section old-report limit must be 0-2")
    new = {path.resolve() for path in newly_parsed_metadata}
    all_metadata = sorted(evidence_root.glob("house_clerk/financial-reports/*/*/*.json"),
                          reverse=True)
    selected_new, selected_old = [], []
    for path in all_metadata:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        relative = _relative(metadata)
        extraction = review_root / EXTRACTIONS / relative
        qualification = review_root / QUALIFICATIONS / relative
        if not extraction.is_file() or not qualification.is_file():
            continue
        if path.resolve() in new:
            # A holding parser replay can change row identities even when the
            # PDF and the section observer schema are unchanged.
            selected_new.append(path)
            continue
        observation = review_root / OBSERVATIONS / relative
        failure = review_root / FAILURES / relative
        if observation.is_file():
            try:
                if json.loads(observation.read_text(encoding="utf-8")).get(
                        "schema_version") == SCHEMA:
                    continue
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
        if not failure.is_file() and len(selected_old) < old_limit:
            selected_old.append(path)
    return selected_new + selected_old


def run_section_batch(evidence_root: Path, review_root: Path,
                      newly_parsed_metadata: list[Path], *, old_limit: int = 2) -> dict:
    selected = select_section_batch(evidence_root, review_root, newly_parsed_metadata,
                                    old_limit=old_limit)
    summary = {"new_selected": 0, "old_selected": 0, "observed": 0,
               "failed": 0, "schedule_a_rows": 0,
               "schedule_b_date_anchored_rows": 0}
    new = {path.resolve() for path in newly_parsed_metadata}
    for metadata_path in selected:
        is_new = metadata_path.resolve() in new
        summary["new_selected" if is_new else "old_selected"] += 1
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        relative = _relative(metadata)
        observation_path = review_root / OBSERVATIONS / relative
        failure_path = review_root / FAILURES / relative
        try:
            extraction = json.loads((review_root / EXTRACTIONS / relative).read_text(
                encoding="utf-8"))
            if (extraction.get("source_sha256") != metadata["sha256"] or
                    extraction.get("source", {}).get("document_id") != metadata["document_id"]):
                raise ValueError("House annual section extraction conflicts with archive")
            expected_archive = (f"house_clerk/financial-reports/{metadata['filing_year']}/"
                                f"{metadata['document_id']}/{metadata['sha256']}.pdf")
            if metadata.get("archive_path") != expected_archive:
                raise ValueError("House annual section archive path is inconsistent")
            pdf_path = evidence_root / metadata["archive_path"]
            observation = observe_archived_annual_pdf(pdf_path, extraction)
            observation_path.parent.mkdir(parents=True, exist_ok=True)
            observation_path.write_text(json.dumps(observation, ensure_ascii=False,
                                                   sort_keys=True, separators=(",", ":")) + "\n",
                                        encoding="utf-8")
            failure_path.unlink(missing_ok=True)
            summary["observed"] += 1
            summary["schedule_a_rows"] += observation["schedule_a_extraction_row_count"]
            summary["schedule_b_date_anchored_rows"] += observation[
                "schedule_b_date_anchored_row_count"]
        except Exception as exc:
            # This lane cannot delete or rewrite a holding extraction/qualification.
            observation_path.unlink(missing_ok=True)
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            failure_path.write_text(json.dumps({
                "schema_version": "house-annual-section-failure/v1",
                "source_sha256": metadata["sha256"],
                "document_id": metadata["document_id"],
                "observer_schema": SCHEMA,
                "error_type": type(exc).__name__,
                "error": str(exc)[:200],
            }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            summary["failed"] += 1
    return summary
