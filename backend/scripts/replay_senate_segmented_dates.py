"""Reconsume fixed source date observations without OCR or source collection."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("output-root","review-root","evidence-root","grid-input","word-input",
                 "previous-candidate","state","baseline-extraction"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--code-root",type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args();sys.path.insert(0,str(args.code_root/"backend/src"))
    from unison_snapshot.senate_paper import extract_archived_paper_reports
    from unison_snapshot.senate_candidate import build_senate_candidate
    from unison_snapshot.disclosure_status_senate import build_senate_status
    from unison_snapshot.builder import build
    from unison_snapshot.disclosure_status import attach_disclosure_status
    out=args.output_root
    if out.exists() and any(out.iterdir()):parser.error("output-root must be empty; on Windows choose a short path")
    out.mkdir(parents=True,exist_ok=True);review=out/"r"
    shutil.copytree(args.review_root,review)
    grids,words=load(args.grid_input),load(args.word_input)
    batch=extract_archived_paper_reports(args.evidence_root,review,grid_observations=grids,
        cached_word_pages=words,selected_document_ids=sorted(grids))
    if batch["failure_count"] or len(batch["extractions"])!=1:
        raise ValueError("fixed date replay did not produce exactly one extraction")
    extraction=batch["extractions"][0];baseline=load(args.baseline_extraction)
    old={r["extraction_id"]:r for r in baseline["transactions"]}
    new={r["extraction_id"]:r for r in extraction["transactions"]}
    if set(old)!=set(new):raise ValueError("source row identities changed")
    changes=[]
    for identifier,before in old.items():
        after=new[identifier]
        protected=set(before)|set(after)
        for key in protected-{"transaction_date","qualification_status","quarantine_reasons","field_observations"}:
            if before.get(key)!=after.get(key):raise ValueError("unrelated source field changed: "+key)
        def old_fields(row):
            return {k:v for k,v in row["field_observations"].items()
                    if k not in {"cached_word_pages_sha256","segmented_date_read"}}
        if old_fields(before)!=old_fields(after):raise ValueError("old raw field observation changed")
        if before["transaction_date"]!=after["transaction_date"]:
            if before["transaction_date"] is not None or not after["field_observations"].get("segmented_date_read"):
                raise ValueError("date change lacks the bound new read")
            changes.append({"id":identifier,"source_slot":after["source_slot"],
                "before":before["transaction_date"],"after":after["transaction_date"]})
    target=review/"senate_efd/extractions"/extraction["document_id"]/extraction["source_sha256"]/(extraction["parser_version"]+".json")
    target.write_text(json.dumps(extraction,ensure_ascii=False),encoding="utf-8")
    previous=load(args.previous_candidate);state=load(args.state)
    candidate,audit=build_senate_candidate(review,state,previous,evidence_root=args.evidence_root)
    old_facts={r["id"]:r for r in previous["transactions"]};facts={r["id"]:r for r in candidate["transactions"]}
    if any(facts.get(k)!=v for k,v in old_facts.items()):raise ValueError("old formal object changed")
    (review/"senate_efd/qualifications/current.json").write_text(json.dumps(audit,ensure_ascii=False),encoding="utf-8")
    scopes=build_senate_status(args.evidence_root,review,candidate["transactions"],[])
    bundle=build(candidate,generated_at=state["run_at"],allow_production=True)
    code_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=args.code_root,text=True).strip()
    # Same bounded archival/status fixture identifiers used in the saved run;
    # this is development attachment validation, never a publication claim.
    attach_disclosure_status(bundle,scopes,transactions=candidate["transactions"],provenance={
        "code_commit":code_commit,"evidence_commit":"80f086267677b8fde34314f24024bdc505d97cb0",
        "review_commit":"869375f8c41571b5ff76ba701cf0a2dcea5afb0f",
        "previous_main_commit":"24c3616977625ef3bbac0b2f5888c9572f396248"})
    again,_=build_senate_candidate(review,state,candidate,evidence_root=args.evidence_root)
    if again["transactions"]!=candidate["transactions"]:raise ValueError("repeat formal facts changed")
    code_files=["backend/src/unison_snapshot/senate_paper_dates.py",
        "backend/src/unison_snapshot/senate_paper.py","backend/src/unison_snapshot/senate_candidate.py"]
    receipt={"code_commit":code_commit,"code_files_sha256":{name:hashlib.sha256((args.code_root/name).read_bytes()).hexdigest()
        for name in code_files},"selected_source":extraction["document_id"],"new_ocr_calls":0,
        "source_rows":len(extraction["transactions"]),"source_eligible":extraction["eligible_transaction_count"],
        "source_quarantined":extraction["quarantined_transaction_count"],"date_changes":changes,
        "old_formal_objects_equal":True,"old_formal_count":len(old_facts),"candidate_count":len(facts),
        "new_ids":sorted(set(facts)-set(old_facts)),"repeat_formal_equal":True,"builder_and_status_attach":"passed",
        "snapshot_id":bundle.manifest["snapshot_id"],"source_status":next(r for r in scopes[0]["reports"]
            if r["report_id"]==extraction["document_id"]),"scope":"fixed source and existing cached consumers; not full catalog/production"}
    for name,value in [("extraction-batch",batch),("candidate",candidate),("audit",audit),("status",scopes),("receipt",receipt)]:
        (out/(name+".json")).write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps({k:v for k,v in receipt.items() if k not in {"source_status","date_changes"}},ensure_ascii=True))


if __name__=="__main__":main()
