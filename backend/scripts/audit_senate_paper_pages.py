"""Read-only inventory of the nine fixed Senate paper PTR viewer archives.

This audits page bytes, visible table grid bands and unqualified OCR signals.
It does not create extractions, candidates, or infer transaction check marks.
Requires Pillow and pinned Tesseract/eng locally; no source network calls.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image

from unison_snapshot.senate_paper import tesseract_words
from unison_snapshot.senate_paper_sample import EVIDENCE_COMMIT, OCR_VERSION


def _git_bytes(repo: Path, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{EVIDENCE_COMMIT}:{path}"], cwd=repo,
        capture_output=True, check=True, timeout=60,
    ).stdout


def _line_centers(image: Image.Image, *, faint: bool = False) -> list[int]:
    gray = image.convert("L")
    if gray.size != (3400, 4400):
        return []
    points = gray.load()
    # Some August scans have faint/broken full-width lines but retain strong
    # horizontal rules inside the asset cell. Keep this as an explicit fallback.
    samples = range(700, 1080, 4) if faint else range(525, 2920, 16)
    minimum = 65 if faint else 95
    darkness = 220 if faint else 100
    ys = [y for y in range(950, 4000)
          if sum(points[x, y] < darkness for x in samples) > minimum]
    groups: list[list[int]] = []
    for y in ys:
        if not groups or y - groups[-1][-1] > 8:
            groups.append([y])
        else:
            groups[-1].append(y)
    return [round(sum(group) / len(group)) for group in groups]


def _longest_grid(lines: list[int]) -> list[int]:
    runs: list[list[int]] = []
    for y in lines:
        if runs and 90 <= y - runs[-1][-1] <= 180:
            runs[-1].append(y)
        else:
            runs.append([y])
    return max(runs, key=len, default=[])


def _cell_words(words: list[dict], y0: int, y1: int, x0: int, x1: int) -> list[dict]:
    selected = [w for w in words
                if y0 + 8 < w["top"] + w["height"] / 2 < y1 - 8
                and x0 <= w["left"] + w["width"] / 2 < x1]
    return sorted(selected, key=lambda w: (round(w["top"] / 25), w["left"]))


def _raw(words: list[dict]) -> str:
    return " ".join(w["text"].strip() for w in words if w["text"].strip())


def _asset_ink(image: Image.Image, y0: int, y1: int) -> int:
    gray = image.convert("L")
    points = gray.load()
    return sum(points[x, y] < 100
               for y in range(y0 + 12, y1 - 12, 3)
               for x in range(635, 1090, 3))


def _page_inventory(image: Image.Image, words: list[dict]) -> dict:
    lines = _line_centers(image)
    grid = _longest_grid(lines)
    line_method = "full_width_dark_projection"
    if len(grid) < 8:
        grid = _longest_grid(_line_centers(image, faint=True))
        line_method = "faint_asset_cell_projection"
    # In this fixed 3400x4400 legacy form, the first transaction form page's
    # grid begins around y=1830 and has two printed example bands. Continued
    # pages begin around y=1250..1370. Record that geometric rule explicitly.
    has_examples = bool(grid and grid[0] > 1700)
    table = len(grid) >= 8
    bands = []
    if table:
        for index, (top, bottom) in enumerate(zip(grid, grid[1:]), start=1):
            if has_examples and index <= 2:
                continue
            asset = _cell_words(words, top, bottom, 590, 1385)
            date = _cell_words(words, top, bottom, 1650, 1940)
            direction = _cell_words(words, top, bottom, 1380, 1665)
            amount = _cell_words(words, top, bottom, 1930, 2925)
            ink = _asset_ink(image, top, bottom)
            ocr_signal = bool(asset or date or direction or amount)
            # In the faint August scan, visually blank bands have sample
            # counts 52..98 while filled bands start at 177. The 150 cutoff
            # remains a signal, never a proof that the band is a trade.
            filled_signal = ocr_signal or ink >= 150
            if not filled_signal:
                continue
            bands.append({
                "grid_band": [top, bottom],
                "grid_slot": index - (2 if has_examples else 0),
                "asset_ocr": _raw(asset),
                "date_ocr": _raw(date),
                "direction_cell_ocr": _raw(direction),
                "amount_cell_ocr": _raw(amount),
                "asset_ink_sample_count": ink,
                "ocr_signal": ocr_signal,
                "ocr_content_signal": bool(asset or date),
                "asset_and_date_ocr_signal": bool(asset and date),
                "disposition": "quarantined_unverified_paper_row",
            })
    return {
        "table_grid_detected": table,
        "grid_detection_method": line_method if table else None,
        "grid_line_centers": grid if table else [],
        "printed_examples_detected": has_examples,
        "filled_grid_band_signal_count": len(bands),
        "ocr_signaled_band_count": sum(band["ocr_signal"] for band in bands),
        "ocr_content_band_count": sum(band["ocr_content_signal"] for band in bands),
        "asset_and_date_ocr_band_count": sum(band["asset_and_date_ocr_signal"]
                                             for band in bands),
        "filled_grid_bands": bands,
        "page_status": "table_rows_unverified" if table else "table_not_detected_or_layout_unreadable",
    }


def audit(repo: Path, executable: str) -> dict:
    version_output = subprocess.run([executable, "--version"], check=True,
                                    capture_output=True, text=True, timeout=15).stdout
    version = version_output.splitlines()[0].strip() if version_output else ""
    if version != OCR_VERSION:
        raise ValueError(f"Expected {OCR_VERSION}, got {version}")
    listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", EVIDENCE_COMMIT,
                             "senate_efd/paper_pages"], cwd=repo, check=True,
                            capture_output=True, text=True, timeout=60).stdout.splitlines()
    manifests = sorted(path for path in listed if path.endswith("/manifest.json"))
    if len(manifests) != 9:
        raise ValueError(f"Expected 9 fixed paper manifests, found {len(manifests)}")
    reports = []
    with tempfile.TemporaryDirectory(prefix="senate-paper-inventory-") as dirname:
        image_path = Path(dirname) / "page.gif"
        for path in manifests:
            manifest = json.loads(_git_bytes(repo, path))
            expected_manifest_hash = manifest.pop("manifest_sha256")
            encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")) + "\n").encode()
            if hashlib.sha256(encoded).hexdigest() != expected_manifest_hash:
                raise ValueError(f"Manifest content hash changed: {path}")
            if manifest["page_count"] != len(manifest["pages"]):
                raise ValueError(f"Manifest page count changed: {path}")
            pages = []
            for number, page in enumerate(manifest["pages"], start=1):
                if page["page_number"] != number or page["document_id"] != manifest["document_id"]:
                    raise ValueError(f"Page sequence changed: {path}")
                content = _git_bytes(repo, page["archive_path"])
                if hashlib.sha256(content).hexdigest() != page["sha256"]:
                    raise ValueError(f"Page bytes changed: {page['archive_path']}")
                image = Image.open(BytesIO(content))
                if image.size != (page["width"], page["height"]):
                    raise ValueError(f"Page dimensions changed: {page['archive_path']}")
                image_path.write_bytes(content)
                words = tesseract_words(image_path, executable=executable)
                pages.append({
                    "page_number": number,
                    "page_sha256": page["sha256"],
                    "source_url": page["source_url"],
                    "ocr_word_count": len(words),
                    **_page_inventory(image, words),
                })
            reports.append({
                "document_id": manifest["document_id"],
                "entrypoint_source_sha256": manifest["entrypoint_source_sha256"],
                "manifest_sha256": expected_manifest_hash,
                "page_count": manifest["page_count"],
                "pages": pages,
            })
    return {
        "schema_version": "senate-paper-page-inventory/v1",
        "evidence_commit": EVIDENCE_COMMIT,
        "ocr_version": version,
        "pillow_version": Image.__version__,
        "report_count": len(reports),
        "page_count": sum(item["page_count"] for item in reports),
        "table_page_count": sum(page["table_grid_detected"] for item in reports for page in item["pages"]),
        "filled_grid_band_signal_count": sum(page["filled_grid_band_signal_count"]
                                             for item in reports for page in item["pages"]),
        "ocr_signaled_band_count": sum(page["ocr_signaled_band_count"]
                                       for item in reports for page in item["pages"]),
        "ocr_content_band_count": sum(page["ocr_content_band_count"]
                                      for item in reports for page in item["pages"]),
        "asset_and_date_ocr_band_count": sum(page["asset_and_date_ocr_band_count"]
                                             for item in reports for page in item["pages"]),
        "reports": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.repo, args.tesseract)
    args.output.write_text(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "reports"},
                     sort_keys=True))


if __name__ == "__main__":
    main()
