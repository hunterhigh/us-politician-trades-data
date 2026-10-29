"""Header recovery is isolated from financial and blank-cell observations."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.ocr_geometry import OcrPage
from unison_snapshot.oge_278t_raster_slots import observe_raster_slots


def word(text, x, y):
    return {"text": text, "x0": x, "x1": x+8, "top": y, "bottom": y+5,
        "ocr_confidence": 95, "block_num": 1, "par_num": 1, "line_num": int(y)}


@unittest.skipUnless(importlib.util.find_spec("pdfplumber"), "optional PDF dependency unavailable")
class HeaderRecoveryTests(unittest.TestCase):
    def observe(self, *, missing_amount=False, saved=False, duplicate=False):
        primary = [word(t,x,10) for t,x in [("OGE",5),("Form",20),("278-T",35),("2024",50)]]
        primary += [word("Description",25,110), word("original asset",25,125), word("$1,001",210,125)]
        if duplicate:
            primary += [word("Type",105,110), word("Type",115,110)]
        extra = [word(t,x,110) for t,x in [("Type",110),("Date",140),("Notification",170),("Amount",210)]
                 if not (missing_amount and t=="Amount") and not (duplicate and t=="Type")]
        # Even a recovery backend returning a financial-looking body word must
        # never add that word to the normal source-slot observation.
        extra.append(word("poison financial text",25,125))
        geometry = {"status":"candidate", "basis":"same_page_image_grid",
            "edges_pdf_points":[0,20,100,130,160,200,250], "header_band_pdf_points":[100,120],
            "physical_row_bands":[[120,140]], "render_coordinate_frame":"displayed_mediabox"}
        census = {"results":[{"page_number":1,"status":"coverage_unknown",
            "column_geometry_observation":geometry}]}
        ocrpage = OcrPage(width=300,height=300,words=primary)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"source.pdf";source.write_bytes(b"mocked PDF")
            page=SimpleNamespace(width=300,height=300,cropbox=(0,0,300,300),mediabox=(0,0,300,300))
            with patch("pdfplumber.open") as opened, patch(
                    "unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback",return_value=(census,{})), patch(
                    "unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages",return_value=([ocrpage],"test engine")), patch(
                    "unison_snapshot.oge_278t_header_ocr.recover_header_words",return_value=(extra,{"use":"header_structure_only"})) as recover:
                opened.return_value.__enter__.return_value.pages=[page]
                result=observe_raster_slots(source,expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    page_numbers=[1], **({"saved_ocr_pages":{1:ocrpage},"saved_ocr_engine":"test engine"} if saved else {}))
            return result, recover.call_count, primary

    def test_only_header_changes_financial_words_and_counts_use_original_source(self):
        result,calls,primary=self.observe()
        page=result["pages"][0]
        self.assertEqual(calls,1)
        self.assertEqual(page["physical_slot_count"],1)
        self.assertEqual(page["ocr_word_count"],len(primary))
        self.assertEqual([w["text"] for w in page["slots"][0]["words"]],["original asset","$1,001"])
        self.assertEqual(page["column_geometry_observation"]["header_role_evidence"]["status"],"confirmed")
        self.assertFalse(result["production_mutation_authorized"])

    def test_missing_or_duplicate_header_remains_unknown(self):
        for args in ({"missing_amount":True},{"duplicate":True}):
            result,_,_=self.observe(**args)
            self.assertIsNone(result["pages"][0]["physical_slot_count"])
            self.assertEqual(result["pages"][0]["slots"],[])

    def test_saved_replay_never_runs_new_header_ocr(self):
        result,calls,_=self.observe(saved=True)
        self.assertEqual(calls,0)
        self.assertEqual(result["pages"][0]["slots"],[])

    def test_recovery_rejects_changed_source_before_pdf_render(self):
        from unison_snapshot.oge_278t_header_ocr import recover_header_words
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"source.pdf";source.write_bytes(b"changed source")
            with self.assertRaisesRegex(ValueError,"differs from archived source"):
                recover_header_words(source,source_sha256="a"*64,page_number=1,
                    geometry={"basis":"same_page_image_grid"},missing_columns=[1],resolution=300,executable=None)


if __name__ == "__main__":
    unittest.main()
