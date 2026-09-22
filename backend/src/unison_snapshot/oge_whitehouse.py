"""Account for White House OGE 278-T and annual 278e catalog entries.

The catalog is discovery evidence only. Request links are never fetched here.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import json
import re
import urllib.error
import urllib.request
from urllib.parse import unquote, urlsplit

from .oge import OgeCatalogError, _classify_278, _type_fragment
from .oge_reports import OgePdfClient, _write_once


SCHEMA = "oge-whitehouse-coverage/v1"
AGENCIES = frozenset({"white house office", "office of the vice president"})
_DIRECT_PATH = re.compile(
    r"/201/Presiden\.nsf/PAS\+Index/([0-9a-f]{32})/\$FILE/[^/]+\.pdf",
    re.IGNORECASE,
)


def _annual_record(markup: str) -> tuple[str, str, str | None, int | None, bool] | None:
    if "Annual" not in markup:
        return None
    # OGE distinguishes a combined annual/termination entry from a plain
    # annual report. Keep it visible but never infer an annual period for it.
    combined = markup.startswith("Annual Term")
    text, anchors = _type_fragment(markup)
    if not text.startswith("Annual"):
        return None
    if len(anchors) != 1:
        raise OgeCatalogError("White House annual report needs one catalog link")
    url, label = anchors[0]
    year_match = re.search(r"Annual \((20\d{2})\)", text)
    year = int(year_match.group(1)) if year_match else None
    if not combined and year is None:
        raise OgeCatalogError("White House annual report has no report year")
    if label == "Request this Document":
        return "request_required", url, None, year, combined
    if not label.startswith("Annual ("):
        raise OgeCatalogError("White House annual direct link has an unexpected label")
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    match = _DIRECT_PATH.fullmatch(path)
    if parsed.hostname != "extapps2.oge.gov" or parsed.query or match is None:
        raise OgeCatalogError("White House annual direct PDF URL is invalid")
    return "direct_pdf", url, match.group(1).lower(), year, combined


def build_whitehouse_coverage(raw_pages: list[tuple[int, dict]]) -> dict:
    """Validate complete archived OGE pages and classify two report families."""
    if not raw_pages:
        raise OgeCatalogError("White House coverage needs catalog pages")
    expected_start = 0
    total = None
    selected = []
    for start, payload in sorted(raw_pages, key=lambda item: item[0]):
        if not isinstance(payload, dict) or set(payload) != {
                "draw", "recordsTotal", "recordsFiltered", "data"}:
            raise OgeCatalogError("White House catalog page shape changed")
        rows = payload["data"]
        count = payload["recordsTotal"]
        if (type(start) is not int or start != expected_start or type(count) is not int or
                count < 0 or payload["recordsFiltered"] != count or
                not isinstance(rows, list) or (total is not None and total != count)):
            raise OgeCatalogError("White House catalog pagination is incomplete")
        total = count
        for offset, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != {
                    "type", "name", "agency", "title", "level", "docDate", "amended"}:
                raise OgeCatalogError("White House catalog row shape changed")
            if not isinstance(row["agency"], str) or row["agency"].strip().casefold() not in AGENCIES:
                continue
            markup = row["type"]
            if not isinstance(markup, str):
                raise OgeCatalogError("White House document type is invalid")
            if "278 Transaction" in markup:
                classified = _classify_278(markup)
                if classified is None:
                    raise OgeCatalogError("White House 278-T is unclassified")
                access, url, document_id, pending = classified
                kind, year, combined = "278t", None, False
            elif "Annual" in markup:
                annual = _annual_record(markup)
                if annual is None:
                    raise OgeCatalogError("White House annual report is unclassified")
                access, url, document_id, year, combined = annual
                kind, pending = "278e_annual", "Pending Final OGE Disposition" in markup
            else:
                continue
            for key in ("name", "agency", "title"):
                if not isinstance(row[key], str) or not row[key].strip():
                    raise OgeCatalogError(f"White House catalog {key} is invalid")
            try:
                added = datetime.strptime(row["docDate"], "%Y-%m-%dT%H:%M:%S").date().isoformat()
            except (TypeError, ValueError):
                raise OgeCatalogError("White House catalog date is invalid") from None
            stable = "|".join((str(start + offset), kind, row["name"], row["agency"], row["title"],
                               row["docDate"], markup))
            selected.append({
                "catalog_index": start + offset,
                "catalog_entry_id": "oge-catalog:" + hashlib.sha256(stable.encode()).hexdigest()[:24],
                "document_type": kind,
                "annual_term_combined": combined,
                "report_year": year,
                "filer_name": row["name"],
                "agency": row["agency"],
                "position_title": row["title"],
                "catalog_added_date": added,
                "amended_label": row["amended"].strip() or None,
                "pending_final_oge_disposition": pending,
                "access_method": access,
                "source_document_id": document_id,
                "document_url": url,
                "source_id": "oge",
            })
        expected_start += len(rows)
    if total is None or expected_start != total:
        raise OgeCatalogError("White House catalog does not cover every source row")
    if len({row["catalog_entry_id"] for row in selected}) != len(selected):
        raise OgeCatalogError("White House catalog has duplicate selected entries")
    return {
        "schema_version": SCHEMA,
        "source_id": "oge",
        "records_total": total,
        "catalog_rows_covered": expected_start,
        "reports": selected,
        "counts": {
            kind: {access: sum(row["document_type"] == kind and row["access_method"] == access
                               for row in selected)
                   for access in ("direct_pdf", "request_required")}
            for kind in ("278t", "278e_annual")
        },
    }


def coverage_from_archive(root: Path, metadata_path: Path) -> dict:
    """Read only pages named by the verified immutable OGE catalog manifest."""
    base = root.resolve()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("schema_version") != "oge-catalog-archive/v1":
        raise OgeCatalogError("White House catalog archive metadata is invalid")
    pages = []
    for item in metadata.get("pages", []):
        path = (base / item["archive_path"]).resolve()
        if base not in path.parents:
            raise OgeCatalogError("White House catalog page escaped archive root")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item["sha256"] or len(content) != item["byte_length"]:
            raise OgeCatalogError("White House catalog page hash does not match")
        pages.append((item["start"], json.loads(content)))
    result = build_whitehouse_coverage(pages)
    if result["records_total"] != metadata["record_count"]:
        raise OgeCatalogError("White House catalog total does not match archive")
    result["catalog_sha256"] = metadata["sha256"]
    result["catalog_retrieved_at"] = metadata["retrieved_at"]
    return result


def latest_catalog_metadata(root: Path) -> Path:
    """Select the newest complete OGE catalog manifest already in evidence."""
    directory = root / "oge" / "catalog"
    candidates = []
    for path in directory.glob("*.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if metadata.get("schema_version") == "oge-catalog-archive/v1":
            candidates.append((metadata["retrieved_at"], path))
    if not candidates:
        raise OgeCatalogError("No archived OGE catalog is available")
    return max(candidates)[1]


ANNUAL_ARCHIVE_SCHEMA = "oge-278e-annual-archive/v1"
_MAX_ANNUAL_BYTES = 50 * 1024 * 1024
_OGE_2026_ANNOUNCEMENT = (
    "https://www2.oge.gov/web/oge.nsf/Resources/"
    "Now%2BAvailable%3A%2BThe%2BPresident%E2%80%99s%2Band%2BVice%2BPresident%E2%80%99s"
    "%2Bcertified%2Bannual%2Bfinancial%2Bdisclosure%2Breports"
)
# OGE also published these two 2026 PDFs in its dated announcement. Their
# content hashes were verified against that public release before pinning.
_OFFICIAL_2026_FALLBACKS = {
    "69aeaa9d7455acd585258e27002ddee1": (
        "https://oge.box.com/shared/static/zycb5i2ny8kssm51uzqm8ygyq2zkpkqq.pdf",
        "84b5987e4c8a418188600bea1e1ba6b44e0d5735cdd757cee5aba551977ca402",
    ),
    "40ce0f66f853096985258e27002ddfbb": (
        "https://oge.box.com/shared/static/o3xuu4cumw5a2pi39ij4jauekltvax4u.pdf",
        "bcad0b4e58789135b758b5a73fc9584ff4bf9bff9cf52b8e4ca9d4189dbe9e3d",
    ),
}


def _download_official_box(url: str, expected_sha: str) -> tuple[bytes, dict[str, str]]:
    if urlsplit(url).hostname != "oge.box.com":
        raise OgeCatalogError("Annual fallback is not an OGE Box URL")
    request = urllib.request.Request(url, headers={"Accept": "application/pdf",
                                                    "User-Agent": "unison-oge-evidence/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            final = urlsplit(response.geturl())
            if response.status != 200 or final.scheme != "https" or final.hostname != "public.boxcloud.com":
                raise OgeCatalogError("OGE Box annual PDF redirected unexpectedly")
            content = response.read(_MAX_ANNUAL_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError):
        raise OgeCatalogError("OGE Box annual PDF is unavailable") from None
    if (len(content) > _MAX_ANNUAL_BYTES or "pdf" not in content_type.lower() or
            not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]):
        raise OgeCatalogError("OGE Box annual PDF is incomplete")
    if hashlib.sha256(content).hexdigest() != expected_sha:
        raise OgeCatalogError("OGE Box annual PDF differs from pinned official release")
    return content, {"content-type": content_type}


def archive_direct_annual_batch(root: Path, coverage: dict, *, limit: int,
                                client: OgePdfClient | None = None) -> dict:
    """Archive catalog-linked annual PDFs; leave Form 201 rows untouched."""
    if coverage.get("schema_version") != SCHEMA or type(limit) is not int or limit < 0:
        raise OgeCatalogError("White House annual archive input is invalid")
    direct = [row for row in coverage["reports"]
              if row["document_type"] == "278e_annual" and row["access_method"] == "direct_pdf"]
    if len({row["source_document_id"] for row in direct}) != len(direct):
        raise OgeCatalogError("White House annual direct IDs are duplicated")
    # Newest reports first; the catalog is an index, never a source of filing dates.
    direct.sort(key=lambda row: (row["report_year"] or 0, row["catalog_index"]), reverse=True)
    base = root.resolve()
    attempted = 0
    archived = []
    failures = []
    for row in direct:
        document_id = row["source_document_id"]
        folder = base / "oge" / "annual" / "reports" / document_id
        existing = sorted(folder.glob("*.json")) if folder.is_dir() else []
        if existing:
            archived.append(json.loads(existing[-1].read_text(encoding="utf-8")))
            continue
        if attempted >= limit:
            continue
        attempted += 1
        try:
            retrieval_url = row["document_url"]
            announcement_url = None
            try:
                content, headers = (client or OgePdfClient()).download(retrieval_url)
            except OgeCatalogError:
                fallback = _OFFICIAL_2026_FALLBACKS.get(document_id)
                if fallback is None or client is not None:
                    raise
                retrieval_url, expected_sha = fallback
                content, headers = _download_official_box(retrieval_url, expected_sha)
                announcement_url = _OGE_2026_ANNOUNCEMENT
            sha = hashlib.sha256(content).hexdigest()
            pdf_path = folder / f"{sha}.pdf"
            _write_once(pdf_path, content)
            metadata = {
                "schema_version": ANNUAL_ARCHIVE_SCHEMA,
                "source_id": "oge",
                "document_id": document_id,
                "catalog_entry_id": row["catalog_entry_id"],
                "catalog_sha256": coverage["catalog_sha256"],
                "document_url": row["document_url"],
                "retrieval_url": retrieval_url,
                "source_announcement_url": announcement_url,
                "filer_name": row["filer_name"],
                "agency": row["agency"],
                "position_title": row["position_title"],
                "report_year": row["report_year"],
                "amended_label": row["amended_label"],
                "pending_final_oge_disposition": row["pending_final_oge_disposition"],
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "sha256": sha,
                "byte_length": len(content),
                "headers": headers,
                "archive_path": pdf_path.relative_to(base).as_posix(),
            }
            metadata_path = folder / f"{sha}.json"
            _write_once(metadata_path, json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")).encode("utf-8"))
            archived.append(metadata)
        except (OgeCatalogError, OSError) as exc:
            failures.append({"document_id": document_id, "error": str(exc)})
    return {
        "schema_version": "oge-278e-annual-archive-batch/v1",
        "direct_count": len(direct),
        "request_required_count": coverage["counts"]["278e_annual"]["request_required"],
        "attempted_count": attempted,
        "archived_count": len(archived),
        "pending_count": len(direct) - len(archived),
        "reports": archived,
        "failures": failures,
    }
