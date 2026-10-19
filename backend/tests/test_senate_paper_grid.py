"""Synthetic consumer wiring checks; these images are not official source gold."""
from copy import deepcopy
import hashlib
from io import BytesIO
import unittest
import tempfile
import json
from unittest.mock import patch
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None

from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_paper import parse_paper_grid_observations, extract_archived_paper_reports
from unison_snapshot.senate_reports import PARSER_VERSION, PAPER_INSPECTION_SCHEMA
from unison_snapshot.senate_paper_observation import _source_report_relations
from unison_snapshot.senate_slots_shadow import classify_slot, TYPE_CELLS, AMOUNT_CELLS
from unison_snapshot.senate_candidate import build_senate_candidate
from tests.test_senate_paper import REPORT, MANIFEST, word, page
from tests import test_senate_candidate as candidate_helpers
from tests.test_senate_candidate import identity, BASE, PARSER


def inputs(*, date="09/01/26", missing_asset_type=True):
    image = Image.new("L", (3400, 4400), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((1414, 2288, 1444, 2318), fill=0)
    draw.rectangle((1965, 2288, 1995, 2318), fill=0)
    def crop(box):
        return {"box_px": box, "grayscale_sha256": hashlib.sha256(image.crop(tuple(box)).tobytes()).hexdigest()}
    source_slot = {"page_number": 2, "grid_slot": 1, "row_bounds_px": [2236, 2370],
                   "asset_ocr_raw": "(S) Acme", "date_ocr_raw": "09/01/26",
                   "asset_ink_sample_count": 200,
                   "row_crop": crop([525, 2236, 2920, 2370]),
                   "asset_crop": crop([590, 2244, 1385, 2362]),
                   "date_crop": crop([1650, 2244, 1940, 2362])}
    slot = classify_slot(source_slot, image,
                         calibrated_centers=([x for _, x in TYPE_CELLS],
                                             [x for _, x in AMOUNT_CELLS], (1650, 1940)),
                         calibration_required=True)
    stream = BytesIO()
    image.save(stream, format="GIF")
    raw = stream.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    manifest = {**MANIFEST, "evidence_complete": True,
                "pages": [{"page_number": 1, "sha256": "b" * 64},
                          {"page_number": 2, "sha256": sha}]}
    observation = {"document_id": REPORT["document_id"], "entrypoint_source_sha256": "a" * 64,
                   "manifest_sha256": manifest["manifest_sha256"], "archive_page_count": 2,
                   "coverage_status": "fixed_slots_observed", "legacy_extraction_failed": True,
                   "legacy_extraction_count": 0, "physical_grid_slot_count": 1,
                   "label_counts": {"data_observed": 1}, "slots": [slot],
                   "pages": [{"page_number": 2, "page_sha256": sha,
                              "physical_grid_slot_count": 1,
                              "label_counts": {"data_observed": 1}}], "unobserved_page_numbers": [1]}
    observation["source_report_ledger"] = _source_report_relations(observation)
    words = [word("(S)", 650, 2280), word("Acme", 760, 2280), word(date, 1730, 2280)]
    if not missing_asset_type:
        words.append(word("(Stock)", 980, 2280))
    words_page = {**page(2, words), "source_sha256": sha}
    return deepcopy(REPORT), manifest, observation, [words_page], {2: raw}


@unittest.skipIf(Image is None, "optional Senate image dependency unavailable")
class SenatePaperGridTests(unittest.TestCase):
    def test_noisy_slot_is_not_a_required_account_but_explicit_unreadable_heading_is(self):
        for heading, confidence, blocked in [(None, 98, False),
                                               ("(S) Account", 98, False),
                                               ("(S) Account", 50, True)]:
            with self.subTest(heading=heading, confidence=confidence):
                args = inputs()
                observation = args[2]
                data = observation["slots"][0]
                data["grid_slot"] = 2
                unknown = deepcopy(data)
                unknown.update(grid_slot=1, row_bounds_px=[2102, 2236], label="unknown",
                               type_marks=[], amount_marks=[], date_ocr_raw="")
                observation["slots"] = [unknown, data]
                observation["physical_grid_slot_count"] = 2
                observation["label_counts"] = {"unknown": 1, "data_observed": 1}
                observation["pages"][0].update(physical_grid_slot_count=2,
                                               label_counts=observation["label_counts"])
                observation["source_report_ledger"] = _source_report_relations(observation)
                if heading:
                    args[3][0]["words"].append(word(heading, 650, 2140, confidence=confidence))
                extraction = parse_paper_grid_observations(*args)
                row = extraction["transactions"][0]
                self.assertEqual("paper_account_scope_unresolved" in row["quarantine_reasons"], blocked)
                if heading and not blocked:
                    self.assertIn("(Account: (S) Account)", row["asset_name_raw"])
                    self.assertEqual(row["account_heading_source_slot"]["grid_slot"], 1)
                elif heading is None:
                    self.assertIsNone(row["account_heading_source_slot"])

    def test_existing_batch_runner_consumes_saved_words_without_ocr(self):
        report, manifest, observation, pages, images = inputs()
        document = report["document_id"]
        for item in manifest["pages"]:
            item["archive_path"] = f"senate_efd/paper_pages/{document}/{item['page_number']}/{item['sha256']}.gif"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, review = root / "evidence", root / "review"
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf8")
            write(review / "status/senate_efd.json", {"catalog_sha256": "c" * 64})
            write(review / "senate_efd/discoveries" / ("c" * 64 + ".json"), {"reports": [report]})
            write(review / "senate_efd/paper_inspections" / document / manifest["entrypoint_source_sha256"] /
                  (PARSER_VERSION + ".json"), {"schema_version": PAPER_INSPECTION_SCHEMA,
                                               "document_id": document,
                                               "source_sha256": manifest["entrypoint_source_sha256"]})
            write(evidence / "senate_efd/paper_pages" / document / "manifest.json", manifest)
            image_path = evidence / manifest["pages"][1]["archive_path"]
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(images[2])
            with patch("unison_snapshot.senate_paper.tesseract_words", side_effect=AssertionError("Unexpected OCR")):
                result = extract_archived_paper_reports(
                    evidence, review, grid_observations={document: observation}, cached_word_pages={document: pages})
            self.assertEqual((result["extraction_count"], result["failure_count"], result["transaction_count"]), (1, 0, 1))
            other = "22222222-2222-4222-8222-222222222222"
            write(review / "senate_efd/discoveries" / ("c" * 64 + ".json"),
                  {"reports": [report, {**report, "document_id": other}]})
            result = extract_archived_paper_reports(
                evidence, review, grid_observations={document: observation},
                cached_word_pages={document: pages}, selected_document_ids=[document])
            self.assertEqual(result["catalog_report_count"], 2)
            self.assertEqual(result["report_count"], 1)
            self.assertEqual(result["scope"], "selected_fixed_reports_not_full_catalog")
            image_path.write_bytes(b"Changed")
            result = extract_archived_paper_reports(
                evidence, review, grid_observations={document: observation}, cached_word_pages={document: pages},
                selected_document_ids=[document])
            self.assertEqual((result["extraction_count"], result["failure_count"]), (0, 1))

    def test_unknown_grid_is_not_a_successful_zero_transaction_report(self):
        args = inputs()
        observation = args[2]
        observation["slots"][0]["label"] = "unknown"
        observation["label_counts"] = {"unknown": 1}
        observation["pages"][0]["label_counts"] = {"unknown": 1}
        observation["source_report_ledger"] = _source_report_relations(observation)
        extraction = parse_paper_grid_observations(*args)
        self.assertEqual(extraction["transactions"], [])
        extraction["parser_version"] = PARSER
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = candidate_helpers.SenateCandidateTests().fixture(
                root, [identity(REPORT["document_id"])], [extraction])
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(candidate["transactions"], [])
            self.assertEqual(audit["empty_eligible_report_count"], 0)
            self.assertEqual(audit["quarantined_reports"][0]["reasons"], ["source_row_coverage_incomplete"])

    def test_grid_fact_reaches_real_candidate_and_retains_missing_type(self):
        args = inputs()
        extraction = parse_paper_grid_observations(*args)
        self.assertEqual(extraction["eligible_transaction_count"], 1)
        self.assertEqual(extraction["transactions"][0]["asset_type_disclosure"], "not_disclosed")
        extraction["parser_version"] = PARSER
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = candidate_helpers.SenateCandidateTests().fixture(
                root, [identity(REPORT["document_id"])], [extraction])
            candidate, audit = build_senate_candidate(root, state, deepcopy(BASE))
            self.assertEqual(len(candidate["transactions"]), 1)
            self.assertEqual(candidate["transactions"][0]["instrument_type"], "Unspecified")
            self.assertEqual(candidate["transactions"][0]["id"], extraction["transactions"][0]["extraction_id"])
            ledger = audit["paper_source_report_ledgers"][0]["ledger"]
            self.assertEqual(ledger["logical_row_count"], 1)
            self.assertEqual(ledger["coverage_unknown_pages"], [1])
            self.assertEqual(audit["partially_qualified_report_count"], 1)
            self.assertEqual(audit["fully_qualified_report_count"], 0)

    def test_unreadable_date_stays_a_row_and_source_conflict_is_retained(self):
        result = parse_paper_grid_observations(*inputs(date="Unclear"))
        self.assertEqual(len(result["transactions"]), 1)
        self.assertEqual(result["quarantined_transaction_count"], 1)
        result = parse_paper_grid_observations(*inputs(date="09/02/26"))
        self.assertIn("paper_date_observation_conflict", result["transactions"][0]["quarantine_reasons"])
        original = parse_paper_grid_observations(*inputs())
        self.assertEqual(result["transactions"][0]["extraction_id"],
                         original["transactions"][0]["extraction_id"])
        args = inputs()
        slot = args[2]["slots"][0]
        expected = "senate-ptr:" + hashlib.sha256("|".join((
            args[1]["entrypoint_source_sha256"], "2", args[3][0]["source_sha256"], "1",
            slot["row_crop_sha256"])).encode("ascii")).hexdigest()[:24]
        self.assertEqual(original["transactions"][0]["extraction_id"], expected)

    def test_source_or_mark_tampering_cannot_create_a_fact(self):
        args = inputs()
        args[2]["slots"][0]["amount_marks"] = ["over_1000000"]
        with self.assertRaisesRegex(SenateEfdError, "mark interpretation"):
            parse_paper_grid_observations(*args)
        args = inputs()
        args[3][0]["source_sha256"] = "f" * 64
        with self.assertRaisesRegex(SenateEfdError, "source images"):
            parse_paper_grid_observations(*args)
        args = inputs()
        args[2]["legacy_extraction_failed"] = False
        with self.assertRaisesRegex(SenateEfdError, "previously failed"):
            parse_paper_grid_observations(*args)


if __name__ == "__main__":
    unittest.main()
