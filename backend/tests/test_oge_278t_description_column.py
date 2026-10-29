"""Synthetic description provenance and OCR integration; not accuracy evidence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oge_content_relations import saved
from unison_snapshot.oge_278t_field_shadow import observe_content_rows, observe_field_shapes


def word(text, left=1, top=2, right=25, bottom=8):
    return {"text": text, "x0": left, "x1": right, "top": top, "bottom": bottom, "ocr_confidence": 92.}


class DescriptionColumnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pdf = self.root / "source.pdf"
        self.pdf.write_bytes(b"synthetic PDF envelope, rendering is mocked")
        self.base = saved()
        self.base["source_sha256"] = hashlib.sha256(self.pdf.read_bytes()).hexdigest()












    def test_numeric_series_and_long_text_diagnostics(self):
        from unison_snapshot.oge_278t_field_shadow import _description_text_difference
        for left,right,kind in [("5.25% 2031", "5.75% 2031", "numeric_tokens_changed"),
                                ("5.25% 2031", "5.25% 2032", "numeric_tokens_changed"),
                                ("SERIES A", "SERIES B", "text_change")]:
            self.assertEqual(_description_text_difference(left,right)["difference_kind"],kind)
        detail = _description_text_difference("A"*3000,"B"*3000)
        self.assertIsNone(detail["changed_spans"])
        self.assertEqual(detail["detail_status"],"omitted_for_long_text")






if __name__ == "__main__":
    unittest.main()
