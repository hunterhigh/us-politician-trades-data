"""Replay one archived annual letter's saved typed OCR; no new recognition.

This verifies source scope, then records the normal native extraction result.
It never creates an empty candidate when native extraction cannot read the cover.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root",type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--pdf",type=Path,required=True)
    parser.add_argument("--metadata",type=Path,required=True)
    parser.add_argument("--saved-tsv",type=Path,required=True)
    args=parser.parse_args()
    sys.path.insert(0,str(args.code_root/"backend/src"))
    from unison_snapshot.oge import OgeCatalogError
    from unison_snapshot.oge_annual import extract_annual_pdf,QUALIFIED_PARSER_VERSION
    from unison_snapshot.oge_annual_amendment_letter import observe_amendment_letter,interpret_amendment_letter
    output=args.output_root
    if output.exists() and any(output.iterdir()):
        parser.error("output-root must be empty to preserve previous receipts")
    output.mkdir(parents=True,exist_ok=True)
    archive=json.loads(args.metadata.read_text(encoding="utf-8"))
    observation=observe_amendment_letter(args.pdf,args.saved_tsv,archive_metadata=archive)
    scope=interpret_amendment_letter(observation,archive_metadata=archive,archived_pdf_path=args.pdf)
    try:
        native=extract_annual_pdf(args.pdf,source_url=archive["document_url"],source_sha256=archive["sha256"],
            expected_filer=archive["filer_name"],parser_version=QUALIFIED_PARSER_VERSION)
        native_result={"status":"parsed","extraction":native,"formal_output_created":False}
    except OgeCatalogError as error:
        native_result={"status":"existing_native_extraction_failed","error_type":type(error).__name__,
            "error":str(error),"formal_output_created":False}
    for name,value in [("observation.json",observation),("scope.json",scope),("native-result.json",native_result)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    receipt={"source_document_id":archive["document_id"],"source_sha256":archive["sha256"],
        "scope_status":scope["status"] if scope else "unresolved", "native_status":native_result["status"],
        "new_ocr_calls":0,"formal_output_created":False,"qualification_changed":False,
        "outputs":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}}
    (output/"receipt.json").write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(receipt,indent=2))


if __name__=="__main__":
    main()
