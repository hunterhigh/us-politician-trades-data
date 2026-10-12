import unittest

from unison_snapshot.house_ptr_slot_relation_shadow import relate_house_ptr_slots


SHA = "a" * 64


def report():
    return {"schema_version": "house-ptr-cell-observation-shadow/v2",
            "source_sha256": SHA,
            "pages": [{"page": 1, "coordinate_frame": "original_page",
                       "rows": [{"physical_band_index": 1,
                                 "band": [.1, .2], "disposition": "data_candidate"},
                                {"physical_band_index": 2,
                                 "band": [.2, .3], "disposition": "unknown"},
                                {"physical_band_index": 3,
                                 "band": [.3, .4], "disposition": "data_candidate"}]}]}


class HousePtrSlotRelationShadowTests(unittest.TestCase):
    def test_old_evidence_links_one_slot_and_keeps_unmatched_candidate(self):
        extraction = {"source_sha256": SHA, "transactions": [
            {"extraction_id": "old-1", "evidence": {"page": 1,
                                                     "bbox_points": [10, 13, 30, 17]}}]}
        result = relate_house_ptr_slots([100], report(), extraction)
        self.assertEqual(result["old_extractions"][0]["physical_slots"],
                         [{"page": 1, "band_index": 1}])
        self.assertEqual(result["slots"][2]["relation_status"],
                         "no_old_extraction_relation")
        self.assertEqual(result["qualification_status"], "unverified_shadow_only")

    def test_multisegment_relation_and_unresolved_piece_are_preserved(self):
        extraction = {"source_sha256": SHA, "transactions": [
            {"extraction_id": "old-1", "evidence": {"segments": [
                {"page": 1, "bbox_points": [10, 13, 30, 17]},
                {"page": 1, "bbox_points": [10, 33, 30, 37]},
                {"page": 1, "bbox_points": [10, 70, 30, 75]}]}}]}
        result = relate_house_ptr_slots([100], report(), extraction)
        old = result["old_extractions"][0]
        self.assertEqual(len(old["physical_slots"]), 2)
        self.assertEqual(old["unresolved_segments"][0]["reason"],
                         "no_unique_physical_band")

    def test_source_and_duplicate_guards(self):
        extraction = {"source_sha256": "b" * 64, "transactions": []}
        with self.assertRaisesRegex(ValueError, "hashes differ"):
            relate_house_ptr_slots([100], report(), extraction)
        extraction = {"source_sha256": SHA, "transactions": [
            {"extraction_id": "same", "evidence": {}},
            {"extraction_id": "same", "evidence": {}}]}
        with self.assertRaisesRegex(ValueError, "repeated"):
            relate_house_ptr_slots([100], report(), extraction)
