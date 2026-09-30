"""Bind one fixed paper PTR to identity and frontend gates without candidates."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image

from unison_snapshot.senate_paper_sample import DOCUMENT_ID, EVIDENCE_COMMIT


REVIEW_COMMIT = "9749718e5ae837057db7c031d431714fc72988d1"
FRONTEND_SHA256 = "d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4"
FIELDS_SHA256 = "d9c964052f9d715decc58b414d1799f6b03e4c8eaeb50359215c9a162d9dc176"
CATALOG_SHA256 = "0cf0622f563dbd276ed05b96424765f9a596515fd494e03bbb2c1b25ed611a63"
IDENTITY_PATH = (f"senate_efd/identities/{CATALOG_SHA256}/"
                 "d26c545c27ea0fc05a55b6317b521e6993f73ff107f4f5166b0a0308998bc7b6.json")
ROSTER_SHA256 = "7ed5a29def7139ac58a30b1e10e68f3c6059cf7938a7c5256c5374c94a5d1d8e"
REPORT_SHA256 = "107c79b1276ae99ef9321547420e2303790fcc3cb110f43bf64e4c3ddb34bb6c"
SOURCE_URL = f"https://efdsearch.senate.gov/search/view/paper/{DOCUMENT_ID}/"
AMOUNT_BANDS = {
    "1001_15000": (1001, 15000),
    "15001_50000": (15001, 50000),
    "50001_100000": (50001, 100000),
    "100001_250000": (100001, 250000),
    "250001_500000": (250001, 500000),
}


def _git_bytes(repo: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"],
                            capture_output=True)
    if result.returncode:
        raise ValueError(f"fixed source blob unavailable: {commit}:{path}")
    return result.stdout


def _json(repo: Path, commit: str, path: str) -> dict:
    result = json.loads(_git_bytes(repo, commit, path))
    if not isinstance(result, dict):
        raise ValueError(f"fixed source object invalid: {path}")
    return result


def _crop(image: Image.Image, box: tuple[int, int, int, int]) -> dict:
    return {"box_px": list(box),
            "grayscale_sha256": hashlib.sha256(image.crop(box).convert("L").tobytes()).hexdigest()}


def _catalog_context(repo: Path) -> tuple[dict, list[dict]]:
    catalog = _json(repo, EVIDENCE_COMMIT, f"senate_efd/catalog/{CATALOG_SHA256}.json")
    if catalog.get("sha256") != CATALOG_SHA256 or catalog.get("record_count") != 131:
        raise ValueError("fixed official catalog binding changed")
    records = []
    for page in catalog["pages"]:
        raw = _git_bytes(repo, EVIDENCE_COMMIT, page["archive_path"])
        if hashlib.sha256(raw).hexdigest() != page["sha256"]:
            raise ValueError("fixed catalog page hash changed")
        records.extend(json.loads(raw)["data"])
    if len(records) != 131:
        raise ValueError("fixed official catalog rows changed")
    identity_batch = _json(repo, REVIEW_COMMIT, IDENTITY_PATH)
    if (identity_batch.get("catalog_sha256") != CATALOG_SHA256 or
            identity_batch.get("roster_sha256") != ROSTER_SHA256):
        raise ValueError("fixed identity batch binding changed")
    identities = {item["document_id"]: item for item in identity_batch["identities"]}
    identity = identities.get(DOCUMENT_ID)
    if (identity is None or identity.get("status") != "matched_automatically" or
            identity.get("person_id") != "senate:B001277" or
            identity.get("filer_name") != "RICHARD BLUMENTHAL"):
        raise ValueError("fixed report identity changed")
    roster = _git_bytes(repo, EVIDENCE_COMMIT, f"senate_efd/members/{ROSTER_SHA256}.xml")
    if hashlib.sha256(roster).hexdigest() != ROSTER_SHA256:
        raise ValueError("fixed official roster hash changed")
    members = [item for item in ET.fromstring(roster).iter("member")
               if item.findtext("bioguide_id") == "B001277"]
    if len(members) != 1 or members[0].findtext("first_name") != "Richard" or members[0].findtext("last_name") != "Blumenthal":
        raise ValueError("official roster identity is not unique")
    same_person = []
    for record in records:
        match = re.search(r'href="(/search/view/(ptr|paper)/([0-9a-f-]{36})/)"', record[3])
        if not match:
            raise ValueError("fixed catalog report link is invalid")
        entry = identities.get(match.group(3))
        if entry and entry.get("person_id") == identity["person_id"]:
            same_person.append({"document_id": match.group(3),
                                "kind": match.group(2),
                                "source_url": "https://efdsearch.senate.gov" + match.group(1),
                                "catalog_listed_date": record[4]})
    selected = [item for item in same_person if item["document_id"] == DOCUMENT_ID]
    if (len(selected) != 1 or selected[0]["source_url"] != SOURCE_URL or
            selected[0]["catalog_listed_date"] != "02/12/2026"):
        raise ValueError("fixed paper report catalog row changed")
    return identity, sorted(same_person, key=lambda item: item["document_id"])


def _frontend_context(path: Path) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FRONTEND_SHA256:
        raise ValueError("latest user HTML baseline changed")
    source = raw.decode("utf-8")
    match = re.search(r"const DATA = (\{.*?\});\s*\n", source, re.S)
    if match is None:
        raise ValueError("latest frontend has no parseable DATA object")
    data = json.loads(match.group(1))
    if not isinstance(data.get("transactions"), list) or not data["transactions"]:
        raise ValueError("latest frontend has no transaction schema sample")
    keys = set(data["transactions"][0])
    expected = {"id", "filing_id", "person_id", "owner", "asset_name",
                "instrument_type", "transaction_type", "transaction_date",
                "filed_at", "amount_low", "amount_high", "source_id",
                "source_url", "verification_status"}
    if not expected <= keys:
        raise ValueError("latest frontend transaction fields changed")
    people = [item for item in data["people"] if item.get("id") == "senate:B001277"]
    if len(people) != 1:
        raise ValueError("fixed matched person absent from latest frontend")
    return {"transaction_keys": sorted(keys), "matched_person_id": people[0]["id"],
            "current_person_transaction_count": sum(
                item.get("person_id") == people[0]["id"]
                for item in data["transactions"]),
            "current_document_transaction_count": sum(
                item.get("filing_id") == DOCUMENT_ID or item.get("source_url") == SOURCE_URL
                for item in data["transactions"])}


def audit(repo: Path, fields_path: Path, frontend_path: Path) -> dict:
    raw_fields = fields_path.read_bytes()
    if hashlib.sha256(raw_fields).hexdigest() != FIELDS_SHA256:
        raise ValueError("fixed Senate field observations changed")
    fields = json.loads(raw_fields)
    if fields.get("document_id") != DOCUMENT_ID or len(fields.get("rows", [])) != 36:
        raise ValueError("fixed Senate field row count changed")
    identity, same_person = _catalog_context(repo)
    frontend = _frontend_context(frontend_path)
    if frontend["current_document_transaction_count"] != 0:
        raise ValueError("latest frontend already contains this paper report")
    prefix = f"senate_efd/reports/{DOCUMENT_ID}/{REPORT_SHA256}"
    metadata = _json(repo, EVIDENCE_COMMIT, prefix + ".metadata.json")
    report_html = _git_bytes(repo, EVIDENCE_COMMIT, prefix + ".html")
    if (hashlib.sha256(report_html).hexdigest() != REPORT_SHA256 or
            metadata.get("document_url") != SOURCE_URL or
            metadata.get("access_method") != "paper_ptr" or
            metadata.get("portal_listed_date") != "2026-02-12" or
            metadata.get("report_amendment_number") is not None):
        raise ValueError("fixed official paper entrypoint changed")
    manifest = _json(repo, EVIDENCE_COMMIT,
                     f"senate_efd/paper_pages/{DOCUMENT_ID}/manifest.json")
    pages = {}
    for number in (1, 2):
        archived = manifest["pages"][number - 1]
        raw = _git_bytes(repo, EVIDENCE_COMMIT, archived["archive_path"])
        if hashlib.sha256(raw).hexdigest() != archived["sha256"]:
            raise ValueError("fixed official cover or form page hash changed")
        pages[number] = Image.open(BytesIO(raw)).convert("L")
    inner = pages[2].crop((1089, 829, 1105, 844))
    inner_dark = sum(pixel < 160 for pixel in inner.get_flattened_data())
    if inner_dark > 3:
        raise ValueError("fixed amendment checkbox interior changed")
    supplement = _json(repo, REVIEW_COMMIT,
                       "senate_efd/amendment_supplements/current.json")
    supplement_manifest = _json(repo, REVIEW_COMMIT, supplement["manifest_path"])
    target_ids = {item["amendment_document_id"] for item in supplement_manifest["targets"]}
    if DOCUMENT_ID in target_ids:
        raise ValueError("fixed report unexpectedly appears in verified amendment supplement")
    report_context = {
        "official_url": SOURCE_URL,
        "entrypoint_sha256": REPORT_SHA256,
        "catalog_sha256": CATALOG_SHA256,
        "catalog_listed_date": "2026-02-12",
        "cover_receipt_date": "2026-02-12",
        "filing_time": None,
        "cover_identity_crop": _crop(pages[1], (350, 1250, 2300, 2000)),
        "form_identity_crop": _crop(pages[2], (500, 750, 2950, 1150)),
        "amendment_checkbox": {"interior_crop": _crop(pages[2], (1089, 829, 1105, 844)),
                               "dark_pixels_below_160": inner_dark,
                               "observation": "no_mark_detected"},
        "identity": {"person_id": identity["person_id"],
                     "filer_name": identity["filer_name"],
                     "review_status": identity["status"],
                     "official_roster_sha256": ROSTER_SHA256,
                     "bioguide_id": "B001277"},
        "same_person_catalog_reports": same_person,
        "same_person_electronic_report_count": sum(item["kind"] == "ptr" for item in same_person),
        "amendment_number_in_entrypoint": None,
        "verified_supplement_target": False,
        "cross_report_equivalence": "unresolved",
    }
    rows = []
    for field in fields["rows"]:
        if field.get("row_field_status") != "four_fields_confirmed_candidate_quarantined":
            raise ValueError("a fixed paper field row is no longer confirmed")
        raw_asset = field["asset"]["ocr"]["joined_text"]
        match = re.fullmatch(r"\((S|J|DC)\) (.+)", raw_asset)
        if match is None:
            raise ValueError("paper owner code observation changed")
        band = field["amount"]["observed_band"]
        if band not in AMOUNT_BANDS:
            raise ValueError("paper amount checkbox maps to unsupported range")
        low, high = AMOUNT_BANDS[band]
        frontend_projection = {
            "id": None,
            "filing_id": DOCUMENT_ID,
            "person_id": identity["person_id"],
            "owner": None,
            "asset_name": None,
            "instrument_type": None,
            "transaction_type": field["direction"]["observed_label"],
            "transaction_date": field["date"]["normalized_date_if_legible"],
            "filed_at": None,
            "amount_low": low,
            "amount_high": high,
            "source_id": "senate_efd",
            "source_url": SOURCE_URL,
            "verification_status": None,
        }
        missing = [key for key in ("id", "owner", "asset_name", "filed_at",
                                    "verification_status")
                   if frontend_projection[key] is None]
        rows.append({
            "page_number": field["page_number"],
            "grid_slot": field["grid_slot"],
            "page_sha256": field["page_sha256"],
            "row_crop_sha256": field["row_crop"]["grayscale_sha256"],
            "asset_cell_verbatim": raw_asset,
            "owner_code_observed": f"({match.group(1)})",
            "asset_text_without_code_observed": match.group(2),
            "amount_band_observed": band,
            "filed_date_observed": "2026-02-12",
            "filed_at_precision": "date",
            "frontend_projection_preview": frontend_projection,
            "missing_frontend_fields": missing,
            "unverified_optional_fields": ["instrument_type", "ticker"],
            "blocking_reasons": [
                "owner_code_has_no_fixed_official_legend_binding",
                "asset_normalization_depends_on_owner_code",
                "filing_time_not_in_fixed_paper_evidence",
                "stable_transaction_id_not_assigned",
                "cross_report_relationship_unresolved",
                "candidate_verification_not_run",
            ],
            "disposition": "quarantined_no_candidate",
            "candidate_transaction_id": None,
        })
    if len(rows) != 36 or len({(item["page_number"], item["grid_slot"])
                                    for item in rows}) != 36:
        raise ValueError("paper frontend gate row accounting changed")
    return {
        "schema_version": "senate-paper-first-report-frontend-gates/v1",
        "evidence_commit": EVIDENCE_COMMIT,
        "review_commit": REVIEW_COMMIT,
        "frontend_sha256": FRONTEND_SHA256,
        "field_observations_sha256": FIELDS_SHA256,
        "document_id": DOCUMENT_ID,
        "frontend_context": frontend,
        "report_context": report_context,
        "row_count": 36,
        "missing_field_counts": dict(Counter(key for row in rows
                                             for key in row["missing_frontend_fields"])),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--fields", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.repo, args.fields, args.frontend)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": result["row_count"],
                      "missing_field_counts": result["missing_field_counts"],
                      "same_person_electronic_report_count": result["report_context"]["same_person_electronic_report_count"]},
                     sort_keys=True))


if __name__ == "__main__":
    main()
