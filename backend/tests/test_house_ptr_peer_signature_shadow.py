import copy
import sys
import unittest
from pathlib import Path

from unison_snapshot.house_ptr_peer_signature_shadow import (
    observe_house_ptr_peer_signatures)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_house_ptr_key_fields_shadow import report


def metadata(document_id, sha):
    return {"source_id": "house_clerk", "filing_type": "P",
            "filing_year": 2026, "document_id": document_id,
            "sha256": sha, "filer_name": "Hon. Example", "state_district": "TX10",
            "filed_date": "2026-06-10",
            "source_url": "https://disclosures-clerk.house.gov/public_disc/"
                          f"ptr-pdfs/2026/{document_id}.pdf"}


class HousePtrPeerSignatureShadowTests(unittest.TestCase):
    def test_exact_shape_match_retains_all_physical_slots(self):
        target = report()
        peer = copy.deepcopy(target)
        peer["source_sha256"] = "b" * 64
        result = observe_house_ptr_peer_signatures(
            target, metadata("9116141", "a" * 64),
            [(metadata("9116211", "b" * 64), peer)])
        self.assertEqual(result["target_data_candidate_count"], 1)
        self.assertEqual(result["peers"][0]["exact_ocr_shape_overlap_count"], 1)
        self.assertEqual(result["peers"][0]["exact_ocr_shape_overlaps"][0]
                         ["target_slots"], [{"page": 1, "physical_band_index": 1}])
        self.assertFalse(result["qualification_authorized"])

    def test_missing_peer_cells_cannot_be_claimed_absent(self):
        target = report()
        peer = copy.deepcopy(target)
        peer["source_sha256"] = "b" * 64
        peer["pages"][0]["rows"][0]["cells"] = None
        peer["pages"][0]["cell_status"] = "unobserved_rotated_frame"
        result = observe_house_ptr_peer_signatures(
            target, metadata("9116141", "a" * 64),
            [(metadata("9116211", "b" * 64), peer)])
        item = result["peers"][0]
        self.assertEqual(item["exact_ocr_shape_overlap_count"], 0)
        self.assertEqual(item["incomplete_or_unobserved_data_slot_count"], 1)
        self.assertEqual(item["unobserved_page_count"], 1)
        self.assertEqual(item["relationship_status"],
                         "unresolved_unverified_source_rows")

    def test_source_and_filer_mismatch_fail_closed(self):
        target = report()
        peer = copy.deepcopy(target)
        wrong = metadata("9116211", "b" * 64)
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            observe_house_ptr_peer_signatures(
                target, metadata("9116141", "a" * 64), [(wrong, peer)])
        peer["source_sha256"] = "b" * 64
        wrong["filer_name"] = "Other"
        with self.assertRaisesRegex(ValueError, "another filer"):
            observe_house_ptr_peer_signatures(
                target, metadata("9116141", "a" * 64), [(wrong, peer)])


if __name__ == "__main__":
    unittest.main()
