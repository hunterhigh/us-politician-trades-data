"""Source-bound typed letter scope, never an income/value replacement.

Saved OCR is an observation, not an approval. Every use reinterprets raw TSV,
checks its source render and proves the six column anchors against source rules.
Unexplained Value pixels remain unknown; this module never produces facts.
"""
from __future__ import annotations
import hashlib
import re
from datetime import datetime
from math import isfinite
from pathlib import Path
from urllib.parse import urlsplit
from .oge import OgeCatalogError

SCHEMA = "oge-annual-local-letter-observation/v1"

def _sha(raw):
    return hashlib.sha256(raw).hexdigest()

def _source_image(path, expected_sha):
    import pdfplumber
    from io import BytesIO
    raw = Path(path).read_bytes()
    if _sha(raw) != expected_sha:
        raise OgeCatalogError("annual letter PDF differs from source")
    with pdfplumber.open(BytesIO(raw)) as pdf:
        page = pdf.pages[0]
        if (page.rotation != 0 or list(page.mediabox) != [0, 0, page.width, page.height] or
                (page.cropbox is not None and list(page.cropbox) != list(page.mediabox))):
            raise OgeCatalogError("annual letter source image transform is unsupported")
        image = page.to_image(resolution=200).original.convert("RGB")
        return image, list(page.lines), float(page.width), float(page.height)

def observe_amendment_letter(pdf_path, tsv_path, *, archive_metadata):
    """Package existing single-page typed OCR; no recognition is run here."""
    image, _, width, height = _source_image(pdf_path, archive_metadata["sha256"])
    raw = Path(tsv_path).read_bytes()
    return {"schema_version": SCHEMA, "source_document_id": archive_metadata["document_id"],
            "source_sha256": archive_metadata["sha256"], "page_number": 1, "dpi": 200,
            "image_size": list(image.size), "page_size": [width, height],
            "image_pixels_sha256": _sha(image.tobytes()), "raw_tsv": raw.decode("utf-8"),
            "raw_tsv_sha256": _sha(raw), "method": "saved_tesseract_eng_psm3_tsv"}

def _words(observation):
    words = []
    lines = observation["raw_tsv"].splitlines()
    if not lines or lines[0] != "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext":
        raise OgeCatalogError("annual letter raw TSV header is invalid")
    for line in lines[1:]:
        fields = line.split("\t", 11)  # quotes are text, never CSV quoting
        if len(fields) != 12 or fields[0] not in {"1", "2", "3", "4", "5"}:
            raise OgeCatalogError("annual letter raw TSV record is invalid")
        if fields[0] != "5" or not fields[11].strip():
            continue
        try:
            x, y, w, h = map(int, fields[6:10])
            confidence = float(fields[10])
        except ValueError:
            raise OgeCatalogError("annual letter raw TSV is invalid") from None
        if min(x, y, w, h) < 0 or w == 0 or h == 0 or not isfinite(confidence) or not 0 <= confidence <= 100:
            raise OgeCatalogError("annual letter raw TSV word is invalid")
        words.append({"text": fields[11], "box": [x*.36, y*.36, (x+w)*.36, (y+h)*.36]})
    return words

def _columns(image, words, lines):
    """Repeated local contrast in header/body binds roles, not empty cells."""
    import numpy as np
    headers = [w for w in words if w["text"] == "Description"]
    if len(headers) != 1:
        return None
    top = headers[0]["box"][1]
    header = [w for w in words if top-2 <= w["box"][1] <= top+17]
    def one(text):
        found = [w["box"] for w in header if w["text"] == text]
        return found[0] if len(found) == 1 else None
    roles = [one("#"), one("Description"), one("EIF"), one("Value")]
    inc = sorted([w["box"] for w in header if w["text"] == "Income"])
    typ, amount = one("Type"), one("Amount")
    if any(r is None for r in roles) or len(inc) != 2 or typ is None or amount is None:
        return None
    roles += [[inc[0][0], inc[0][1], typ[2], typ[3]],
              [inc[1][0], inc[1][1], max(inc[1][2], amount[2]), amount[3]]]
    hb = max(b[3] for b in roles)
    # Long native horizontal rule must span every role; no hardcoded document box.
    bottoms = [l for l in lines if abs(l["top"]-l["bottom"]) < .01 and
               l["top"] > hb and l["x0"] < roles[0][0] and l["x1"] > roles[-1][2]]
    if len(bottoms) != 1:
        return None
    bottom = bottoms[0]; left, right = bottom["x0"], bottom["x1"]
    body = [w for w in words if hb+2 < w["box"][1] < bottom["top"] and
            left < w["box"][0] and w["box"][2] < right]
    if not body:
        return None
    bt = min(w["box"][1] for w in body)
    a = np.asarray(image.convert("L")); scale = .36
    xs = list(range(round((left-3)/scale), round((right+3)/scale)+1))
    hit = []
    for x in xs:
        ok = True
        for lo, hi in [(top+3, hb+1), (bt+1, bottom["top"]-3)]:
            edges = np.linspace(round(lo/scale), round(hi/scale), 6).astype(int)
            supported = 0
            for y,z in zip(edges, edges[1:]):
                bg = np.median(np.concatenate([a[y:z,x-17:x-8],a[y:z,x+8:x+17]],axis=1),axis=1)
                supported += float(np.mean(bg-np.min(a[y:z,x-1:x+2],axis=1)>=12)) >= .15
            ok &= supported >= 4
        if ok:
            hit.append(x)
    groups = []
    for x in hit:
        if not groups or x > groups[-1][-1]+1:
            groups.append([])
        groups[-1].append(x)
    tracks = [[g[0]*scale,g[-1]*scale] for g in groups]
    if len(tracks) != 7 or any(b-a>4 for a,b in tracks):
        return None
    if not all(tracks[i][1] < (b[0]+b[2])/2 < tracks[i+1][0] for i,b in enumerate(roles)):
        return None
    return {"tracks": tracks, "header_boxes": roles, "body_words": body,
            "value_status": "unknown_boundary_fringe_not_proven_blank"}

def interpret_amendment_letter(observation, *, archive_metadata, archived_pdf_path):
    """Return a narrowly proven target or None; never trusts supplied scope labels."""
    if not isinstance(observation, dict):
        return None
    archive = archive_metadata
    if (not archive.get("amended_label") or
            urlsplit(archive.get("document_url", "")).hostname != "extapps2.oge.gov" or
            "AMENDED" not in archive.get("document_url", "").upper()):
        return None
    if (observation.get("schema_version") != SCHEMA or observation.get("page_number") != 1 or
            observation.get("dpi") != 200 or observation.get("source_document_id") != archive.get("document_id") or
            observation.get("source_sha256") != archive.get("sha256") or
            not isinstance(observation.get("raw_tsv"), str) or
            _sha(observation["raw_tsv"].encode()) != observation.get("raw_tsv_sha256")):
        raise OgeCatalogError("annual amendment letter source binding is invalid")
    image, lines, width, height = _source_image(archived_pdf_path, archive["sha256"])
    if (observation.get("image_size") != list(image.size) or observation.get("page_size") != [width,height] or
            observation.get("image_pixels_sha256") != _sha(image.tobytes())):
        raise OgeCatalogError("annual amendment letter render binding is invalid")
    words = _words(observation)
    if any(w["box"][2] > width or w["box"][3] > height for w in words):
        raise OgeCatalogError("annual letter word exceeds source page")
    text = " ".join(w["text"] for w in words)
    purpose = re.search(r"The purpose of this letter is to correct the amount of the advance I reported on Part 2, line ([1-9]\d*) of my financial disclosure report that I signed on ([A-Za-z]+ \d{1,2}, \d{4})\. The correct amount is noted below\. Correction to Part 2", text)
    if purpose is None:
        return None
    try:
        signed = datetime.strptime(purpose[2], "%B %d, %Y").date().isoformat()
    except ValueError:
        return None
    # No additional correction scope may hide elsewhere in the observed letter.
    remaining = text[:purpose.start()] + text[purpose.end():]
    remaining = re.sub(r"Re: Amendment to Financial Disclosure Report of .*? Dear Mr\. Rounds:", "", remaining)
    remaining = remaining.replace("I understand that this amendment will be attached to and become part of my financial disclosure report", "")
    if re.search(r"\b(correct(?:ion)?|change|replace|revise|Part\s+\d|line\s+\d)\b", remaining, re.I):
        return None
    columns = _columns(image, words, lines)
    if columns is None:
        return None
    body, tracks = columns["body_words"], columns["tracks"]
    def in_column(w,index):
        return tracks[index][1] < w["box"][0] and w["box"][2] < tracks[index+1][0]
    printed = [w["text"] for w in body if in_column(w,0)]
    types = [w["text"] for w in body if in_column(w,4)]
    if printed != [purpose[1]] or types != ["advance"]:
        return None
    description_raw = " ".join(w["text"] for w in body if in_column(w,1))
    unknown_note = re.search(r"value not readily ascertainable", description_raw, re.I)
    return {"status": "local_target_value_unresolved", "section": "part2", "printed_row_number": purpose[1],
            "original_report_signed_date": signed, "corrected_column_role": "Income Amount",
            "holding_value_status": columns["value_status"],
            "official_unknown_value_note_raw": unknown_note[0] if unknown_note else None,
            "source_document_id": archive["document_id"],
            "source_sha256": archive["sha256"], "page_number": 1,
            "raw_tsv_sha256": observation["raw_tsv_sha256"], "image_pixels_sha256": observation["image_pixels_sha256"],
            "purpose_raw": purpose[0], "column_evidence": columns,
            "field_application": "none_income_not_a_frontend_value", "formal_corrections": []}
