from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from unison_snapshot.house import HouseIndexError

HAS_IMAGE_DEPS = (importlib.util.find_spec("PIL") is not None and
                  importlib.util.find_spec("pdfplumber") is not None)
if HAS_IMAGE_DEPS:
    from PIL import Image, ImageDraw
    from unison_snapshot.house_ptr_grid_shadow import _grid_rows, build_grid_shadow


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(HAS_IMAGE_DEPS, "optional House image audit dependencies unavailable")
class HousePtrGridShadowTests(unittest.TestCase):
    def test_grid_lines_and_asset_ink_yield_physical_rows(self):
        image = Image.new("RGB", (1000, 1000), "white")
        draw = ImageDraw.Draw(image)
        for y in (200, 250, 300, 350):
            draw.line((70, y, 900, y), fill="black", width=2)
        for upper in (200, 250, 300):
            draw.rectangle((160, upper + 10, 330, upper + 35), fill="black")
        rows = _grid_rows(image, {"table_top": .2, "table_bottom": .35,
                                  "asset_x0": .16, "asset_x1": .41})
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(ink > .04 for _, _, ink in rows))

    def test_missing_asset_ink_fails_closed(self):
        image = Image.new("RGB", (1000, 1000), "white")
        draw = ImageDraw.Draw(image)
        for y in (200, 250, 300):
            draw.line((70, y, 900, y), fill="black", width=2)
        draw.rectangle((160, 210, 330, 235), fill="black")
        with self.assertRaisesRegex(HouseIndexError, "no independently visible asset ink"):
            _grid_rows(image, {"table_top": .2, "table_bottom": .3,
                               "asset_x0": .16, "asset_x1": .41})

    def test_wrong_source_fails_before_render(self):
        with self.assertRaisesRegex(HouseIndexError, "source hash mismatch"):
            build_grid_shadow(b"wrong pdf", {}, {}, {
                "schema_version": "house-ptr-grid-sample/v1",
                "source_sha256": "0" * 64,
            })

    def test_committed_samples_conserve_rows_and_retain_candidates(self):
        expected = {"9116290": (11, 4, 7, [5, 6],
                                ["house-ptr:c2a121352254ef9a9983f263"]),
                    "9115813": (9, 1, 8, [4, 5], [])}
        for document_id, (physical, extracted, shadow, pages, candidates) in expected.items():
            with self.subTest(document_id=document_id):
                result = json.loads((ROOT / "docs" /
                    f"house-ptr-grid-{document_id}-shadow.json").read_text(encoding="utf-8"))
                before = deepcopy(result)
                rows = result["physical_rows"]
                self.assertEqual(len(rows), physical)
                self.assertEqual(len({row["locator"] for row in rows}), physical)
                self.assertEqual(result["counts"]["stored_extraction_rows"], extracted)
                self.assertEqual(result["counts"]["shadow_quarantined_unrecognized_rows"], shadow)
                self.assertEqual(result["coverage"]["observed_page_minimum_rows"], pages)
                self.assertEqual(result["coverage"]["retained_candidate_ids"], candidates)
                self.assertEqual(result["coverage"]["unhandled_row_lower_bound"], shadow)
                self.assertEqual(sum(row["stored_extraction_id"] is not None for row in rows),
                                 extracted)
                self.assertTrue(all(row["disposition"] == "quarantined_shadow_unrecognized"
                                    for row in rows if row["stored_extraction_id"] is None))
                self.assertEqual(result, before)


if __name__ == "__main__":
    unittest.main()
