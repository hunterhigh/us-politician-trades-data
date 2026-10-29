"""Trusted glyph fingerprints and meanings; contains no font binary."""
import json
from pathlib import Path

REFERENCE_SHA256 = 'cd30fda05b9923bc425dc1e8f4a81ca3230c973610980dd5f322e41e829901df'
REFERENCE = json.loads((Path(__file__).resolve().parents[2] /
                        'data/font_references/georgia.json').read_bytes())
