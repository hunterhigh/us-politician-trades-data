"""Extract only page code from the user-designated HTML sample.

Run locally when that exact file is available. CI uses the checked-in template
and never needs the 68 MB frozen data payload or the user's Downloads folder.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys


SOURCE_SHA256 = "d60282832dc0e38e47be900fdd37aa386db474df404989467ffb8b55367efaa4"
MARKER = b"const DATA = "
TAIL = b";\nconst PEOPLE = "
PLACEHOLDER = b"__LATEST_HTML_DATA_JSON__"


def extract(source: Path, output: Path) -> tuple[int, str]:
    payload = source.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != SOURCE_SHA256:
        raise ValueError(f"Latest HTML source hash differs: {actual}")
    if payload.count(MARKER) != 1:
        raise ValueError("Latest HTML must have exactly one DATA assignment")
    start = payload.index(MARKER) + len(MARKER)
    end = payload.index(TAIL, start)
    if payload.count(TAIL) != 1 or end <= start:
        raise ValueError("Latest HTML DATA boundary is ambiguous")
    template = payload[:start] + PLACEHOLDER + payload[end:]
    if (template[:start] != payload[:start] or
            template[start + len(PLACEHOLDER):] != payload[end:]):
        raise AssertionError("Template changed page code outside DATA")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(template)
    return end - start, hashlib.sha256(template).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: extract_latest_html_template.py SOURCE_HTML OUTPUT_TEMPLATE")
    removed, template_sha = extract(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"DATA bytes removed={removed}; template_sha256={template_sha}")
