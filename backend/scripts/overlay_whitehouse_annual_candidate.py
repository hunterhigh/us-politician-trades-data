"""Overlay the complete White House annual review subset on the OGE candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import os
import hashlib
import importlib.metadata
import importlib.util

from unison_snapshot.whitehouse_annual_candidate import overlay_whitehouse_annual_candidate


def _runtime_input(path: Path, root: Path) -> tuple[dict, dict]:
    """Bind a portable input's existing path fields before any output mutation."""
    from unison_snapshot.oge_handwritten_date import MODEL_SHA256, CONFIG_SHA256

    raw = path.read_bytes()
    value = json.loads(raw)
    root = root.resolve(strict=True)
    if not root.is_dir() or not isinstance(value, dict):
        raise ValueError("Annual runtime requires an input object and a directory")
    metadata = value.get("archive_metadata_by_document", {})
    receipt = {"input_sha256": hashlib.sha256(raw).hexdigest(), "files": [], "dependencies": {}}
    checked = set()
    for key in ("saved_scan_observations_by_document", "handwritten_date_inputs_by_document",
                "amendment_letter_observations_by_document"):
        bundles = value.get(key, {})
        if not isinstance(bundles, dict):
            raise ValueError(f"Annual runtime requires a document mapping: {key}")
        for document, bundle in bundles.items():
            if not isinstance(bundle, dict):
                raise ValueError(f"Annual runtime requires an object for {document}")
            for field in ("archived_pdf_path", "model_directory"):
                if field not in bundle:
                    continue
                reference = bundle[field]
                if not isinstance(reference, str) or not reference or Path(reference).is_absolute():
                    raise ValueError(f"Annual runtime {field} must be relative to its supplied root")
                resolved = (root / reference).resolve(strict=True)
                if not resolved.is_relative_to(root):
                    raise ValueError(f"Annual runtime {field} escapes its supplied root")
                if field == "archived_pdf_path":
                    expected = metadata.get(document, {}).get("sha256")
                    files = [(resolved, expected)]
                else:
                    files = [(resolved / "model.onnx", MODEL_SHA256), (resolved / "config.json", CONFIG_SHA256)]
                    for module, distribution in (("pymupdf", "PyMuPDF"), ("numpy", "numpy"),
                            ("PIL", "Pillow"), ("cv2", "opencv-python"), ("onnxruntime", "onnxruntime")):
                        if importlib.util.find_spec(module) is None:
                            raise ValueError(f"Annual handwritten runtime dependency missing: {distribution}")
                        receipt["dependencies"][distribution] = importlib.metadata.version(distribution)
                for target, expected in files:
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    if not isinstance(expected, str) or digest != expected:
                        raise ValueError(f"Annual runtime hash differs: {target.relative_to(root)}")
                    relative = target.relative_to(root).as_posix()
                    if relative not in checked:
                        receipt["files"].append({"path": relative, "sha256": digest})
                        checked.add(relative)
                bundle[field] = str(resolved)
    receipt["files"].sort(key=lambda item: item["path"])
    return value, receipt


def _write(path: Path, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                     allow_nan=False).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                     suffix=".tmp", delete=False) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--evidence-root", type=Path,
                        help="Controlled archive required when PTR replacement references exist")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--audit-out", required=True, type=Path)
    parser.add_argument("--oge-annual-input", type=Path,
                        help="Optional saved OGE annual extractions, archive metadata and date observations")
    parser.add_argument("--oge-annual-runtime-root", type=Path,
                        help="Optional prepared root for relative PDF/model paths; verifies hashes before writing")
    args = parser.parse_args()
    if args.oge_annual_runtime_root and not args.oge_annual_input:
        parser.error("--oge-annual-runtime-root requires --oge-annual-input")
    annual_input, runtime = None, None
    if args.oge_annual_input:
        if args.oge_annual_runtime_root:
            annual_input, runtime = _runtime_input(args.oge_annual_input, args.oge_annual_runtime_root)
        else:
            annual_input = json.loads(args.oge_annual_input.read_bytes())
    candidate = json.loads(args.candidate.read_bytes())
    result, audit = overlay_whitehouse_annual_candidate(candidate, args.review_root, evidence_root=args.evidence_root,
        **({"oge_annual_input": annual_input} if annual_input is not None else {}))
    if runtime is not None:
        audit["oge_annual"]["runtime"] = runtime
    _write(args.candidate, result)
    _write(args.audit_out, audit)
    print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    main()
