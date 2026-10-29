"""Bounded cell producer/consumer mechanisms; synthetic pixels are not OCR gold."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import sys
import unittest
from unittest.mock import patch

from PIL import Image
from test_oge_content_relations import saved, EDGES
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_field_shadow import observe_content_rows


def primary(raw="salo", crossing=True):
    data=saved([["1","ALPHA",raw,"01/02/2026","No","$1,001 - $15,000"]])
    page=data["pages"][0]
    page["render_coordinate_frame"]="displayed_mediabox"
    words=page["slots"][0]["words"]
    words[1]["bbox_pdf_points"]=[11,102,40,108]
    words[2]["bbox_pdf_points"]=[69.7 if crossing else 71,102,76,108]
    page["slots"][0].update(_observe_slot_columns(words,EDGES,page=page))
    return data
