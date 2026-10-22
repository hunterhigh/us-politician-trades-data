"""Contracts for optional source replay, not a handwriting accuracy benchmark."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot import oge_handwritten_date as reader
from unison_snapshot.oge_annual_ledger import annual_filing_date_evidence
from test_oge_annual_candidate import source, inputs, base
from unison_snapshot.whitehouse_annual_candidate import overlay_oge_annual_candidate


class HandwrittenDateTests(unittest.TestCase):
    def recognize(self, raw="5.8.23", *, score=.99, annual_year=2022, extra=None):
        symbols = [{"kind": "digit" if c.isdigit() else c,
                    "crop_box": [i * 20, 0, i * 20 + 10, 60]} for i, c in enumerate(raw)]
        tokens = [{"char": c, "score": score if c.isdigit() else .7,
                   "frame": i * 5, "crop_x": i * 20} for i, c in enumerate(raw)]
        if extra:
            extra(symbols, tokens)
        with patch.object(reader, "_source_field", return_value=(object(), {"page_count": 15})), \
                patch.object(reader, "_ink_symbols", return_value=(object(), symbols, [])), \
                patch.object(reader, "_read_ctc", return_value=(tokens, {})):
            return reader.observe_annual_filer_date("unused", source_sha256="a" * 64,
                annual_year=annual_year, model_directory="unused")

    def test_all_digits_and_physical_punctuation_are_required(self):
        result = self.recognize()
        self.assertEqual(result["date"], "2023-05-08")
        self.assertEqual(result["source_reference"]["raw_text"], "5.8.23")
        self.assertIsNone(self.recognize(score=.94)["date"])
        self.assertIsNone(self.recognize(extra=lambda s, t: t.pop())["date"])
        self.assertIsNone(self.recognize(extra=lambda s, t: s.pop())["date"])
        self.assertIsNone(self.recognize(extra=lambda s, t: t[2].update(crop_x=999))["date"])
        self.assertIsNone(self.recognize(extra=lambda s, t: s[1].update(kind="-"))["date"])

    def test_calendar_and_century_do_not_repair_bad_readings(self):
        for raw in ("2.30.23", "13.5.23", "5.8.2", "5.8.2021", "5.8/23", "S.8.23"):
            self.assertIsNone(self.recognize(raw)["date"], raw)
        self.assertIsNone(self.recognize(annual_year=1999)["date"])
        self.assertEqual(self.recognize("5.8.2023")["date"], "2023-05-08")

    def test_no_ink_does_not_invoke_model(self):
        with patch.object(reader, "_source_field", return_value=(object(), {})), \
                patch.object(reader, "_ink_symbols", return_value=(None, [], ["blue_date_ink_absent"])), \
                patch.object(reader, "_read_ctc") as model:
            result = reader.observe_annual_filer_date("unused", source_sha256="a" * 64,
                annual_year=2022, model_directory="unused")
        self.assertIsNone(result["date"])
        model.assert_not_called()

    def test_bundle_never_accepts_manual_date_or_review_crop(self):
        for bundle in ({}, {"date": "2023-05-08"},
                       {"archived_pdf_path": "a", "model_directory": "b", "role": "agency_review"},
                       {"archived_pdf_path": "a", "model_directory": "b", "date": "2023-05-08"}):
            with self.assertRaises(ValueError):
                reader.replay_annual_filer_date({}, bundle)

    def test_pdf_hash_is_checked_before_reading_roles(self):
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            self.skipTest("optional PDF reader unavailable")
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "wrong.pdf"
            pdf.write_bytes(b"not the archived PDF")
            with self.assertRaisesRegex(ValueError, "archived source"):
                reader._source_field(pdf, "a" * 64)

    def test_model_and_configuration_are_both_pinned(self):
        try:
            import onnxruntime  # noqa: F401
            import numpy  # noqa: F401
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("optional handwriting runtime unavailable")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "model.onnx").write_bytes(b"different weights")
            (root / "config.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "pinned bytes"):
                reader._read_ctc(object(), root)

    def test_native_contradiction_is_not_silently_overwritten(self):
        extraction = source()
        with patch.object(reader, "replay_annual_filer_date", return_value={"date": "2026-06-02"}):
            with self.assertRaisesRegex(ValueError, "dates conflict"):
                annual_filing_date_evidence(extraction, handwritten_date_input={})

    def test_overlay_replays_optional_source_without_mutating_raw_rows(self):
        extraction = source()
        extraction["filing_date"] = None
        extraction["cover_date_evidence"] = None
        original = deepcopy(extraction)
        request = inputs(extraction)
        doc = extraction["source_document_id"]
        request["handwritten_date_inputs_by_document"] = {doc: {"archived_pdf_path": "source.pdf", "model_directory": "model"}}
        observed = {"date": "2026-06-01", "method": "automatic_blue_ink_filer_ctc", "source_reference": {"role": "filer_signature"}}
        with patch.object(reader, "replay_annual_filer_date", return_value=observed) as replay:
            candidate, audit = overlay_oge_annual_candidate(base(), request)
        replay.assert_called_once_with(extraction, request["handwritten_date_inputs_by_document"][doc])
        self.assertEqual(audit["reports"][0]["filing_date_evidence"], observed)
        self.assertEqual(len(candidate["reported_holdings"]), 2)
        self.assertEqual(extraction, original)
        legacy, _ = overlay_oge_annual_candidate(base(), inputs(extraction))
        self.assertFalse(legacy["transactions"])
        self.assertFalse(legacy["reported_holdings"])


if __name__ == "__main__":
    unittest.main()
