"""Source-bound blue-ink annual cover dates; no receipt-date substitution.

Optional local reader. Weights are not bundled or downloaded by consumers.
Use naeyn/de-htr-web-v2 revision 6241c1ff0fd42725e0dc46fcd3217469cac851bb
(Apache-2.0); both model and configuration bytes are pinned below. Black or
mixed-ink handwriting and inseparable glyphs remain unresolved.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

SCHEMA = "oge-annual-blue-filer-date/v1"
MODEL_SHA256 = "b576a0a1281b9be46b2574028b75575e041b7d7cb650f063886e733467cc1499"
CONFIG_SHA256 = "4ebd69d9d27b398ea3997b031318accc13123f6a8950def1fedc6e364cb758fa"
DIGIT_THRESHOLD = .95


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_field(pdf_path: Path, source_sha256: str) -> tuple[object, dict]:
    import pymupdf
    from PIL import Image

    data = pdf_path.read_bytes()
    if _hash(data) != source_sha256:
        raise ValueError("handwritten date PDF differs from archived source")
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        page = pdf[0]
        blocks = page.get_text("blocks")
        filers = [b for b in blocks if re.search(r"Filer['’]s\s+Certification", b[4], re.I)]
        agencies = [b for b in blocks if re.search(r"Agency\b", b[4], re.I)
                    and re.search(r"Op\w*on|Opm\w*on", b[4], re.I)]
        if len(filers) != 1 or len(agencies) != 1 or agencies[0][1] <= filers[0][3]:
            raise ValueError("unique native filer and following agency roles are required")
        filer, agency = filers[0], agencies[0]
        labels = [w for w in page.get_text("words") if w[4].rstrip(":").lower() == "date"
                  and filer[3] <= w[1] < agency[1]]
        if len(labels) != 1:
            raise ValueError("unique native Date label in filer role is required")
        label = labels[0]
        box = [label[2] + 2, label[1] - 2, page.rect.width * .95, agency[1] - 2]
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("filer date region is invalid")
        pix = page.get_pixmap(matrix=pymupdf.Matrix(300 / 72, 300 / 72),
                              clip=pymupdf.Rect(box), colorspace=pymupdf.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return image, {"source_sha256": source_sha256, "page_number": 1,
            "page_count": len(pdf), "role": "filer_signature", "region_points": box,
            "renderer": "PyMuPDF/" + pymupdf.VersionBind, "dpi": 300,
            "field_rgb_sha256": _hash(image.tobytes()), "field_size": list(image.size)}


def _ink_symbols(image: object) -> tuple[object | None, list[dict], list[str]]:
    """Account for source punctuation and independently separated digit bodies."""
    import cv2
    import numpy as np
    from PIL import Image

    rgb = np.asarray(image).astype("int16")
    mask = ((rgb[:, :, 2] - rgb[:, :, 0] >= 25) &
            (rgb[:, :, 2] - rgb[:, :, 1] >= 15) & (rgb[:, :, 0] < 200))
    if not mask.any():
        return None, [], ["blue_date_ink_absent"]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype("uint8"), connectivity=8)
    components = [(i, int(x), int(y), int(w), int(h), int(area))
                  for i, (x, y, w, h, area) in enumerate(stats) if i]
    major = [h for _, _, _, _, h, area in components if area >= 100]
    if not major:
        return None, [], ["blue_date_glyphs_unresolved"]
    height = float(np.median(major))
    body = mask.copy()
    symbols = []
    for i, x, y, w, h, area in components:
        kind = ("." if max(w, h) <= height * .30 and .5 <= w / h <= 2 else
                "-" if h <= height * .30 and w / h >= 2 and w < height else None)
        if kind:
            symbols.append({"kind": kind, "box": [x, y, x + w, y + h]})
            body[labels == i] = False
        elif area < 3:
            return None, [], ["unassigned_blue_ink"]
    columns = np.where(body.any(axis=0))[0]
    groups = []
    for x in columns:
        if not groups or x > groups[-1][-1] + 1:
            groups.append([int(x)])
        else:
            groups[-1].append(int(x))
    for xs in groups:
        left, right = xs[0], xs[-1] + 1
        ys = np.where(body[:, left:right].any(axis=1))[0]
        top, bottom = int(ys.min()), int(ys.max()) + 1
        if bottom - top < .65 * height:
            return None, [], ["unresolved_blue_glyph_fragment"]
        symbols.append({"kind": "digit", "box": [left, top, right, bottom]})
    symbols.sort(key=lambda s: sum(s["box"][::2]) / 2)
    kinds = "".join("d" if s["kind"] == "digit" else s["kind"] for s in symbols)
    if not re.fullmatch(r"d{1,2}([.-])d{1,2}\1d{2}(?:d{2})?", kinds):
        return None, symbols, ["source_date_symbol_layout_unresolved"]
    # Exclude mixed black/blue dates. Only long thin printed horizontal rules
    # and isolated scan specks may remain after accounting for blue ink edges.
    dark = (np.asarray(image.convert("L")) < 120).astype("uint8")
    dark[cv2.dilate(mask.astype("uint8"), np.ones((3, 3), "uint8")) > 0] = 0
    _, _, residual, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    ys, xs = np.where(mask)
    rules = [(int(y), int(y + h)) for x, y, w, h, area in residual[1:]
             if w >= image.width * .5 and h <= image.height * .1 and w / h >= 20]
    above = [end for start, end in rules if end <= ys.min()]
    below = [start for start, end in rules if start > ys.max()]
    if not above or not below:
        return None, symbols, ["source_date_enclosing_rules_unresolved"]
    top_rule, bottom_rule = max(above), min(below)
    for x, y, w, h, area in residual[1:]:
        if y + h <= top_rule or y >= bottom_rule:
            continue
        if area >= max(12, height * height * .01):
            return None, symbols, ["unassigned_nonblue_ink"]
    box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    clean = np.where(mask[:, :, None], rgb, 255).astype("uint8")
    crop = Image.fromarray(clean).crop(box)
    for symbol in symbols:
        x, y, r, b = symbol["box"]
        symbol["crop_box"] = [x - box[0], y - box[1], r - box[0], b - box[1]]
    return crop, symbols, []


def _read_ctc(image: object, model_directory: Path) -> tuple[list[dict], dict]:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

    model, config = model_directory / "model.onnx", model_directory / "config.json"
    if _hash(model.read_bytes()) != MODEL_SHA256 or _hash(config.read_bytes()) != CONFIG_SHA256:
        raise ValueError("handwritten date model or configuration differs from pinned bytes")
    settings = json.loads(config.read_bytes())
    width = max(4, round(image.width * 64 / image.height))
    if width > settings["max_width"]:
        raise ValueError("handwritten date exceeds model width")
    resized = image.convert("L").resize((width, 64), Image.Resampling.BILINEAR)
    padded = Image.new("L", ((width + 3) // 4 * 4, 64), 255)
    padded.paste(resized, (0, 0))
    tensor = np.asarray(padded, dtype="float32")[None, None] / 255
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    logits = session.run([settings["output_name"]], {settings["input_name"]: tensor})[0][0]
    tokens, previous = [], -1
    for frame, label in enumerate(logits.argmax(-1)):
        if label != previous and label != settings["blank_index"]:
            scores = np.exp(logits[frame] - logits[frame].max())
            scores /= scores.sum()
            tokens.append({"char": settings["alphabet"][int(label) - 1], "frame": frame,
                           "score": float(scores[label]), "crop_x": frame * 4 * image.width / width})
        previous = label
    return tokens, {"model_sha256": MODEL_SHA256, "config_sha256": CONFIG_SHA256,
        "decoder": "greedy_ctc_no_lexicon", "digit_threshold": DIGIT_THRESHOLD,
        "input_sha256": _hash(tensor.tobytes()), "input_shape": list(tensor.shape),
        "crop_rgb_sha256": _hash(image.tobytes())}


def observe_annual_filer_date(pdf_path: str | Path, *, source_sha256: str,
                              annual_year: int, model_directory: str | Path) -> dict:
    """Recompute from original bytes; no caller-supplied date or crop is trusted."""
    image, reference = _source_field(Path(pdf_path), source_sha256)
    result = {"schema_version": SCHEMA, "date": None, "method": "automatic_blue_ink_filer_ctc",
              "source_reference": reference, "reasons": []}
    crop, symbols, reasons = _ink_symbols(image)
    reference["symbols"] = symbols
    if reasons:
        result["reasons"] = reasons
        return result
    tokens, model = _read_ctc(crop, Path(model_directory))
    reference.update(model)
    reference["tokens"] = tokens
    raw = "".join(t["char"] for t in tokens)
    reference["raw_text"] = raw
    if len(tokens) != len(symbols):
        result["reasons"] = ["ctc_source_symbol_count_conflict"]
        return result
    for token, symbol in zip(tokens, symbols, strict=True):
        numeric = symbol["kind"] == "digit"
        if ((numeric and (token["char"] not in "0123456789" or token["score"] < DIGIT_THRESHOLD)) or
                (not numeric and token["char"] != symbol["kind"]) or
                not symbol["crop_box"][0] <= token["crop_x"] <= symbol["crop_box"][2]):
            result["reasons"] = ["ctc_source_symbol_or_confidence_conflict"]
            return result
    match = re.fullmatch(r"(\d{1,2})([.-])(\d{1,2})\2(\d{2}|\d{4})", raw)
    if not match:
        result["reasons"] = ["recognized_date_syntax_invalid"]
        return result
    month, _, day, year_text = match.groups()
    year = int(year_text)
    # The two year digits are visible, not missing. This reader supports only
    # the current annual-report century; it never invents a year from metadata.
    if len(year_text) == 2:
        if type(annual_year) is not int or not 2000 <= annual_year <= 2099:
            result["reasons"] = ["two_digit_year_century_unresolved"]
            return result
        year += 2000
    try:
        parsed = date(year, int(month), int(day))
    except ValueError:
        result["reasons"] = ["recognized_date_calendar_invalid"]
        return result
    if type(annual_year) is not int or parsed.year < annual_year:
        result["reasons"] = ["filing_date_precedes_annual_period"]
        return result
    result["date"] = parsed.isoformat()
    return result


def replay_annual_filer_date(extraction: dict, bundle: dict) -> dict:
    """Local input paths cause a fresh source replay, never accept normalized facts."""
    if not isinstance(bundle, dict) or set(bundle) != {"archived_pdf_path", "model_directory"}:
        raise ValueError("handwritten date input requires only source and model paths")
    result = observe_annual_filer_date(bundle["archived_pdf_path"],
        source_sha256=extraction["source_sha256"], annual_year=extraction["annual_year"],
        model_directory=bundle["model_directory"])
    if result["source_reference"]["page_count"] != extraction["page_count"]:
        raise ValueError("handwritten date PDF page inventory differs from extraction")
    result["source_reference"]["source_document_id"] = extraction["source_document_id"]
    return result
