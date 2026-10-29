"""Date whitespace interpretation needs physical order and existing slashes."""
from copy import deepcopy
import unittest
from test_oge_dual_axis_fields import dual_input, bind_secondary
from unison_snapshot.oge_278t_fields import _ordered_date_shape, observe_content_rows
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.whitehouse_278t import _saved_supported_fields


def split_date():
    data=dual_input();page=data["pages"][0];slot=page["slots"][0]
    date_index=slot["column_observation"]["transaction_date"]["word_indices"][0]
    box=slot["words"][date_index]["bbox_pdf_points"]
    left,top,right,bottom=box;middle=(left+right)/2
    slot["words"][date_index]={"text":"/2025","bbox_pdf_points":[middle,top,right,bottom]}
    slot["words"].append({"text":"7/11","bbox_pdf_points":[left,top+.5,middle-.2,bottom+.5]})
    slot.update(_observe_slot_columns(slot["words"],page["column_edges_pdf_points"],page=page))
    return data


class DateReadingGeometryTests(unittest.TestCase):
    def test_split_source_runs_order_without_inserting_characters(self):
        data=split_date();before=deepcopy(data)
        content=observe_content_rows([data],document_page_count=3)
        row=content["field_observations"]["pages"][0]["slots"][0]
        self.assertEqual(row["raw_columns"]["transaction_date"],"/2025 7/11")
        self.assertEqual(row["field_shapes"]["transaction_date"]["value"],"2025-07-11")
        self.assertEqual(content["slot_content"][0]["relation_evidence"]["status"],"supported_observation_relation")
        slot=data["pages"][0]["slots"][0]
        cells=[slot["column_observation"][name]["raw_ocr_text"] for name in
               ("printed_number","description","type","transaction_date","notification","amount")]
        good,bad=parse_table_rows([(2,cells)],source_sha=data["source_sha256"],default_owner="Undisclosed",allow_exchange=True)
        self.assertFalse(good)
        projected=_saved_supported_fields(bad[0],row,source_sha=data["source_sha256"])
        self.assertEqual(projected["transaction_date"],"2025-07-11")
        self.assertEqual(projected["extraction_id"],bad[0]["extraction_id"])
        self.assertEqual(projected["cells"],bad[0]["cells"]);self.assertEqual(data,before)

    def test_no_missing_separator_year_inference_or_month_day_swap(self):
        for text in ("7/1512025","7 11/2025","7/11/25","13/7/2025","2/30/2025", "7/11/2025 7/12/2025","7/11/2025 note"):
            reading={"status":"geometry_ordered_unverified","ordered_ocr_text":text,
                     "reading_coordinate_basis":"source_and_row_top_relative_consistency/v1","line_word_indices":[[0]]}
            with self.subTest(text=text):self.assertIsNone(_ordered_date_shape(reading)["value"])
        reading={"status":"geometry_ordered_unverified","ordered_ocr_text":"7 / 11 / 2025",
                 "reading_coordinate_basis":"source_coordinates_only","line_word_indices":[[0]]}
        self.assertIsNone(_ordered_date_shape(reading)["value"])
        reading["reading_coordinate_basis"]="source_and_row_top_relative_consistency/v1"
        reading["line_word_indices"]=[[0],[1]]
        self.assertIsNone(_ordered_date_shape(reading)["value"])

    def test_other_valid_date_remains_a_conflict(self):
        data=split_date();secondary=bind_secondary(data,"transaction_date","7/12/2025")
        content=observe_content_rows([data],document_page_count=3,secondary_observations=[secondary])
        self.assertIn("transaction_date_observation_conflict",content["slot_content"][0]["relation_evidence"]["blocking_reasons"])


if __name__=="__main__":unittest.main()
