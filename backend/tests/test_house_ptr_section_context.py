import unittest

from unison_snapshot.house_ptr_section_context import relate_house_ptr_sections


def text_cell(raw):
    return {"raw_text": raw, "crop_sha256": "b" * 64}


def heading_cells(title, *, marked=False):
    return {"asset": text_cell(title), "owner": text_cell("SP"),
            "event_date": text_cell(""), "notification_date": text_cell(":"),
            "direction": [{"interior_dark_fraction": .1 if marked else 0}],
            "amount": [{"interior_dark_fraction": 0}]}


class HousePaperSectionContextTests(unittest.TestCase):
    def test_heading_carries_across_page_and_rest_for_new_heading(self):
        observation = {"schema_version": "house-ptr-cell-observation-shadow/v3",
                       "qualification_status": "unverified_shadow_only",
                       "source_sha256": "a" * 64,
                       "pages": [
                           {"page": 1, "rows": [
                               {"physical_band_index": 1, "disposition": "unknown",
                                "cells": heading_cells("FAMILY INVESTMENTS LP")},
                               {"physical_band_index": 2, "disposition": "data_candidate",
                                "cells": None}]},
                           {"page": 2, "rows": [
                               {"physical_band_index": 1, "disposition": "data_candidate",
                                "cells": None},
                               {"physical_band_index": 2, "disposition": "unknown",
                                "cells": heading_cells("FAMILY TRUST")},
                               {"physical_band_index": 3, "disposition": "data_candidate",
                                "cells": None}]}]}
        result = relate_house_ptr_sections(observation)
        self.assertEqual((result["physical_slot_count"], result["section_heading_count"],
                          result["data_candidate_count"], result["unclassified_unknown_count"]),
                         (5, 2, 3, 0))
        keys = [row["section_key"] for row in result["data_rows"]]
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[1], keys[2])
        self.assertEqual(result["sections"][0]["title_crop_sha256"], "b" * 64)

    def test_marked_or_last_unknown_row_stays_unclassified(self):
        observation = {"schema_version": "house-ptr-cell-observation-shadow/v3",
                       "qualification_status": "unverified_shadow_only",
                       "source_sha256": "a" * 64,
                       "pages": [{"page": 1, "rows": [
                           {"physical_band_index": 1, "disposition": "unknown",
                            "cells": heading_cells("UNCLEAR ASSET", marked=True)},
                           {"physical_band_index": 2, "disposition": "data_candidate",
                            "cells": None},
                           {"physical_band_index": 3, "disposition": "unknown",
                            "cells": heading_cells("LAST HEADING")}] }]}
        result = relate_house_ptr_sections(observation)
        self.assertEqual(result["section_heading_count"], 0)
        self.assertEqual(result["unclassified_unknown_count"], 2)
        self.assertIsNone(result["data_rows"][0]["section_key"])
