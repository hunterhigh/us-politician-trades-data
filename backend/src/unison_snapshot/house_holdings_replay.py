"""Upgrade saved House Schedule A extractions using their archived originals."""
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

from .house_holdings import PARSER_VERSION, parse_archived_financial_report, qualify_financial_report
from .house import HouseIndexError


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def pending(evidence, review):
    result = []
    base = review / "house_clerk/holding_extractions"
    for path in sorted(base.glob("*/*/*.json")):
        relative = path.relative_to(base)
        extraction = read(path)
        source = extraction["source"]
        expected = Path(str(source["filing_year"])) / source["document_id"] / (extraction["source_sha256"] + ".json")
        if relative != expected:
            raise HouseIndexError("Saved annual extraction has an inconsistent source path")
        qualification = review / "house_clerk/holding_qualifications" / relative
        proof = review / "house_clerk/holding_source_observations" / relative
        if extraction.get("parser_version") != PARSER_VERSION or not qualification.is_file() or not proof.is_file():
            result.append(relative)
    return result


def _reparse(job):
    evidence, metadata, identity = job
    observation = {}
    extraction = parse_archived_financial_report(Path(evidence), metadata, source_observation=observation)
    return extraction, qualify_financial_report(extraction, identity), observation


def replay_batch(evidence, review, *, parser_commit, batch_size=25, workers=2):
    if not 1 <= batch_size <= 25 or not 1 <= workers <= 2:
        raise ValueError("Annual replay requires a bounded batch and at most two workers")
    queued = pending(evidence, review)
    selected = queued[:batch_size]
    jobs = [(str(evidence), read(evidence / "house_clerk/financial-reports" / rel),
             read(review / "house_clerk/holding_identities" / rel)) for rel in selected]
    if workers == 1:
        results = map(_reparse, jobs)
        pool = None
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        results = pool.map(_reparse, jobs)
    try:
        for rel, (extraction, qualification, observation) in zip(selected, results, strict=True):
            prior_path = review / "house_clerk/holding_qualifications" / rel
            prior = read(prior_path) if prior_path.is_file() else {}
            current = {r["id"]: r for r in qualification["holdings"]}
            if any(current.get(row["id"]) != row for row in prior.get("holdings", [])):
                raise HouseIndexError("Annual parser upgrade would change an existing qualified holding")
            for folder, value in (("holding_extractions", extraction),
                                  ("holding_qualifications", qualification),
                                  ("holding_source_observations", observation)):
                write(review / "house_clerk" / folder / rel, value)
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    remaining = pending(evidence, review)
    status_path = review / "status/house_holdings.json"
    status = read(status_path)
    qualifications = [read(p) for p in (review / "house_clerk/holding_qualifications").glob("*/*/*.json")]
    status.update(generated_at=datetime.now(timezone.utc).isoformat(), parser_commit=parser_commit,
        parser_version=PARSER_VERSION, pending_parser_upgrade_count=len(remaining),
        qualification_count=len(qualifications),
        eligible_report_count=sum(q["production_eligible"] for q in qualifications),
        partial_report_count=sum("holding_coverage" in q for q in qualifications),
        ineligible_report_count=sum(not q["production_eligible"] for q in qualifications),
        qualified_holding_count=sum(len(q["holdings"]) for q in qualifications),
        quarantined_row_count=sum(len(q["quarantined"]) for q in qualifications),
        excluded_row_count=sum(len(q["excluded"]) for q in qualifications))
    status["status"] = "attention" if (remaining or status["quarantined_row_count"] or
                                         status.get("failure_count") or status.get("pending_parse_count")) else "ok"
    write(status_path, status)
    return {"parser_version": PARSER_VERSION, "selected": len(selected), "remaining": len(remaining),
            "requires_assembly": bool(selected) or status.get("candidate_parser_version") != PARSER_VERSION,
            "eligible_reports": status["eligible_report_count"], "holdings": status["qualified_holding_count"]}
