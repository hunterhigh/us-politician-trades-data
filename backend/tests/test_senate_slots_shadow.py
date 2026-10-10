"""Fixed archive checks for the Senate paper slot classification shadow."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from unison_snapshot.senate_slots_shadow import (
    EVIDENCE_COMMIT, classify_ledger, combine_date_reads, fixed_archive_pages,
    tesseract_date_cell, tesseract_date_consensus,
)


REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


class SenateSlotsShadowTests(unittest.TestCase):
    def test_strict_date_consensus_retains_disagreement(self):
        self.assertEqual(combine_date_reads({
            "a": {"parsed": "2026-01-09"},
            "b": {"parsed": "2026-01-09"},
            "c": {"parsed": "2026-04-09"},
        })["status"], "conflict")
        agreed = combine_date_reads({
            "a": {"parsed": None},
            "b": {"parsed": "2026-01-09"},
            "c": {"parsed": "2026-01-09"},
        })
        self.assertEqual((agreed["status"], agreed["date"]),
                         ("agreement", "2026-01-09"))
        self.assertEqual(combine_date_reads({
            "a": {"parsed": "2026-01-09"},
            "b": {"parsed": None},
        })["status"], "insufficient")

    @classmethod
    def setUpClass(cls):
        target = (f"{EVIDENCE_COMMIT}:senate_efd/paper_pages/"
                  "068274e1-4b7a-4453-a242-563dde4c10d8/manifest.json")
        if subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", target],
                          capture_output=True).returncode:
            raise unittest.SkipTest("fixed Senate paper archive unavailable")
        inventory = json.loads((DOCS / "senate-paper-52-page-inventory.json")
                               .read_text(encoding="utf-8"))
        cls.read_page = fixed_archive_pages(REPO, inventory)

    def _ledger(self, name: str) -> dict:
        return json.loads((DOCS / name).read_text(encoding="utf-8"))

    def test_first_report_observed_data_matches_visual_audit_without_promotion(self):
        original = self._ledger("senate-paper-first-report-rows.json")
        report = classify_ledger(original, type(self).read_page)["reports"][0]
        self.assertEqual(report["physical_grid_slot_count"], 65)
        self.assertEqual(report["label_counts"], {
            "blank_appearance": 20,
            "data_observed": 36,
            "heading_candidate": 4,
            "unknown": 5,
        })
        self.assertTrue(all(row["candidate_transaction_id"] is None
                            for row in report["slots"]))
        by_location = {(row["page_number"], row["grid_slot"]): row
                       for row in report["slots"]}
        for row in original["rows"]:
            if row["disposition"] == "transaction_observed_quarantined":
                self.assertEqual(by_location[(row["page_number"], row["grid_slot"])]
                                 ["label"], "data_observed")

    def test_shifted_scan_remains_unknown(self):
        ledger = self._ledger("senate-paper-viewer-remainders.json")
        ledger["reports"] = [report for report in ledger["reports"]
                             if report["document_id"] ==
                             "ec20cd93-6702-4a29-b3a6-983f4b17f365"]
        report = classify_ledger(ledger, type(self).read_page)["reports"][0]
        self.assertEqual(report["physical_grid_slot_count"], 48)
        self.assertEqual(report["label_counts"], {"unknown": 48})
        self.assertTrue(all(row["candidate_transaction_id"] is None
                            for row in report["slots"]))

    def test_page_column_calibration_recovers_only_mark_observations(self):
        ledger = self._ledger("senate-paper-viewer-remainders.json")
        ledger["reports"] = [report for report in ledger["reports"]
                             if report["document_id"] ==
                             "ec20cd93-6702-4a29-b3a6-983f4b17f365"]
        report = classify_ledger(ledger, type(self).read_page,
                                 calibrate_columns=True)["reports"][0]
        self.assertEqual(report["label_counts"], {
            "blank_appearance": 5,
            "data_observed": 31,
            "heading_candidate": 2,
            "unknown": 10,
        })
        self.assertEqual([page["column_calibration"]["status"]
                          for page in report["pages"]],
                         ["verified", "verified", "partial"])
        self.assertEqual(report["pages"][2]["column_calibration"]
                         ["calibrated_slot_count"], 11)
        self.assertEqual(sum(row["column_calibration"] == "unknown"
                             for row in report["slots"]), 6)
        self.assertTrue(all(row["candidate_transaction_id"] is None
                            for row in report["slots"]))

    def test_calibration_preserves_first_visually_checked_data_slots(self):
        original = self._ledger("senate-paper-first-report-rows.json")
        report = classify_ledger(original, type(self).read_page,
                                 calibrate_columns=True)["reports"][0]
        self.assertEqual(report["label_counts"]["data_observed"], 36)
        self.assertTrue(all(page["column_calibration"]["status"] == "verified"
                            for page in report["pages"]))
        by_location = {(row["page_number"], row["grid_slot"]): row
                       for row in report["slots"]}
        for row in original["rows"]:
            if row["disposition"] == "transaction_observed_quarantined":
                self.assertEqual(by_location[(row["page_number"], row["grid_slot"])]
                                 ["label"], "data_observed")

    def test_key_cell_crops_are_bound_to_each_physical_slot(self):
        original = self._ledger("senate-paper-first-report-rows.json")
        original["rows"] = [row for row in original["rows"]
                            if row["page_number"] == 2]
        original["physical_grid_slot_count"] = len(original["rows"])
        report = classify_ledger(original, type(self).read_page,
                                 calibrate_columns=True)["reports"][0]
        self.assertEqual(len(report["slots"]), 14)
        row = next(row for row in report["slots"] if row["grid_slot"] == 2)
        self.assertEqual(len(row["key_cells"]["direction"]), 3)
        self.assertEqual(len(row["key_cells"]["amount"]), 11)
        self.assertEqual(row["type_marks"], ["sale"])
        self.assertEqual(row["amount_marks"], ["1001_15000"])
        self.assertEqual(row["key_cells"]["date"]["geometry"], "calibrated")
        self.assertEqual(row["key_cells"]["date"]["page_ocr_parsed"],
                         "2026-01-09")
        self.assertIsNone(row["key_cells"]["date"]["cell_ocr"])
        self.assertTrue(all(len(cell["grayscale_sha256"]) == 64
                            for cell in row["key_cells"]["direction"] +
                            row["key_cells"]["amount"]))

    @unittest.skipUnless(TESSERACT.exists(), "bounded Tesseract unavailable")
    def test_bounded_date_cell_ocr_on_checked_page(self):
        original = self._ledger("senate-paper-first-report-rows.json")
        original["rows"] = [row for row in original["rows"]
                            if row["page_number"] == 2]
        original["physical_grid_slot_count"] = len(original["rows"])
        report = classify_ledger(
            original, type(self).read_page, calibrate_columns=True,
            date_reader=lambda image: tesseract_date_cell(
                image, executable=str(TESSERACT)))["reports"][0]
        data = [row for row in report["slots"] if row["label"] == "data_observed"]
        self.assertEqual(len(data), 8)
        self.assertEqual(sum(row["key_cells"]["date"]["cell_ocr_parsed"]
                             is not None for row in data), 8)
        self.assertTrue(all(not row["key_cells"]["date"]["conflict"]
                            for row in data))

    @unittest.skipUnless(TESSERACT.exists(), "bounded Tesseract unavailable")
    def test_offset_page_visual_date_freeze_keeps_unreadable_cells_unresolved(self):
        # Visual dates were read from the fixed archived image before running
        # the new OCR preprocessing experiment. These are test truth, not
        # candidate facts. Each calibrated date crop SHA is pinned as well.
        expected = {
            2: ("2026-07-08", "dfb49c6ba0bc55e2884154e84f12042040b7e5a5b5ac5cf794e3472a2fac3947"),
            3: ("2026-07-09", "b971ac4b1f51b0ef1d9c3ddd8cd86b2ffd7a89f120db46c162b332e2c81789a4"),
            4: ("2026-07-10", "1eedbaadfa995faf50a3c665ce2ed7d7fa3532c39d3dc1e3dfb0931488c53831"),
            5: ("2026-07-13", "928d07e0429e2bdcb8b1dd733a4d56881a22b2de10357057deae05c19f2eb907"),
            6: ("2026-07-14", "16cd79aa1a20094cb6f8b77d072d49e25c830ced48e4d4bf4a8ed7e750b07ff7"),
            7: ("2026-07-15", "4b935e7b6bfcec50a43051b9fd365d23de638eb09d08d51c4f97bb7483b01ed9"),
            10: ("2026-07-08", "a6dcc68f157feeeb331b49e58ff3e687d071e6217284c9ba1a051f6359aea3fc"),
            11: ("2026-07-08", "b15e7f529707cce85e0a539ec2d8fbcfa48cca7ac2a688077815e238da305d1f"),
            12: ("2026-07-09", "8e2f1da6da9f586a9873a8b33d2fa1deac3a54ff450b0ba615403dcc40fdba81"),
            13: ("2026-07-10", "911916fabf32b4d06dd2cb478df3b92bf4cfa8a92080853cfe0a393b8643adf5"),
            14: ("2026-07-13", "9bcea37a96f3279441c21319d1b05d8627a9de9fef4343bbac7880c79d09a008"),
        }
        ledger = self._ledger("senate-paper-viewer-remainders.json")
        report = next(report for report in ledger["reports"]
                      if report["document_id"] ==
                      "ec20cd93-6702-4a29-b3a6-983f4b17f365")
        report["slots"] = [slot for slot in report["slots"]
                           if slot["page_number"] == 2]
        report["physical_grid_slot_count"] = len(report["slots"])
        ledger["reports"] = [report]
        observed = classify_ledger(
            ledger, type(self).read_page, calibrate_columns=True,
            date_reader=lambda image: tesseract_date_consensus(
                image, executable=str(TESSERACT)))["reports"][0]
        data = {slot["grid_slot"]: slot for slot in observed["slots"]
                if slot["label"] == "data_observed"}
        self.assertEqual(set(data), set(expected))
        self.assertEqual(observed["pages"][0]["page_sha256"],
                         "f741fec8af63a3f0fb7d6304aef2dcaf453c506e73990b25c51430599c60fba7")
        for grid_slot, (visual_date, crop_sha) in expected.items():
            cell = data[grid_slot]["key_cells"]["date"]
            self.assertEqual(cell["grayscale_sha256"], crop_sha)
            if cell["cell_ocr_parsed"] is not None:
                self.assertEqual(cell["cell_ocr_parsed"], visual_date)
            self.assertIsNone(data[grid_slot]["candidate_transaction_id"])
        self.assertEqual(sum(data[slot]["key_cells"]["date"]["cell_ocr"]
                             ["status"] == "agreement" for slot in expected), 3)
        self.assertEqual(sum(data[slot]["key_cells"]["date"]["cell_ocr"]
                             ["status"] != "agreement" for slot in expected), 8)

    def test_all_fixed_grid_slots_have_one_page_level_label(self):
        reports = []
        for name in ("senate-paper-first-report-rows.json",
                     "senate-paper-viewer-remainders.json",
                     "senate-paper-legacy-extracted-grid.json"):
            reports.extend(classify_ledger(self._ledger(name),
                                           type(self).read_page,
                                           calibrate_columns=True)["reports"])
        self.assertEqual(len(reports), 9)
        self.assertEqual(sum(report["physical_grid_slot_count"]
                             for report in reports), 704)
        self.assertEqual(sum(len(report["pages"]) for report in reports), 43)
        self.assertEqual(sum(slot["label"] == "unknown" for report in reports
                             for slot in report["slots"]), 60)
        for report in reports:
            self.assertEqual(sum(report["label_counts"].values()),
                             report["physical_grid_slot_count"])
            for page in report["pages"]:
                self.assertEqual(sum(page["label_counts"].values()),
                                 page["physical_grid_slot_count"])
        self.assertTrue(all(slot["candidate_transaction_id"] is None
                            for report in reports for slot in report["slots"]))
        self.assertTrue(all("key_cells" in slot and "date" in slot["key_cells"]
                            for report in reports for slot in report["slots"]))


if __name__ == "__main__":
    unittest.main()
