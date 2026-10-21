"""Source Type semantics reach the existing projection without rewriting raw IDs."""
from copy import deepcopy
import unittest

from test_oge_dual_axis_fields import dual_input, bind_secondary
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_278t_field_shadow import observe_content_rows
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.whitehouse_278t import _saved_supported_fields


def literal_input(text="purchaso"):
    data=dual_input();page=data["pages"][0];slot=page["slots"][0]
    indices=slot["column_observation"]["type"]["word_indices"]
    assert len(indices)==1
    slot["words"][indices[0]]["text"]=text
    slot.update(_observe_slot_columns(slot["words"],page["column_edges_pdf_points"],page=page))
    slot["raw_ocr_text"]=" ".join(w["text"] for w in slot["words"])
    return data


class TypeLiteralConsumptionTests(unittest.TestCase):
    def test_exact_literal_preserves_raw_cells_and_extraction_identity(self):
        data=literal_input();before=deepcopy(data)
        content=observe_content_rows([data],document_page_count=3)
        row=content["field_observations"]["pages"][0]["slots"][0]
        shape=row["field_shapes"]["type"]
        self.assertEqual(shape["value"],"purchase")
        self.assertEqual(shape["literal_interpretation"]["raw_text"],"purchaso")
        self.assertFalse(shape["literal_interpretation"]["is_glyph_or_unicode_repair"])
        self.assertEqual(content["slot_content"][0]["relation_evidence"]["status"],"supported_observation_relation")
        slot=data["pages"][0]["slots"][0]
        cells=[slot["column_observation"][name]["raw_ocr_text"] for name in
               ("printed_number","description","type","transaction_date","notification","amount")]
        good,bad=parse_table_rows([(2,cells)],source_sha=data["source_sha256"],default_owner="Undisclosed",allow_exchange=True)
        self.assertFalse(good);self.assertEqual(len(bad),1)
        projected=_saved_supported_fields(bad[0],row,source_sha=data["source_sha256"])
        self.assertEqual(projected["transaction_type"],"purchase")
        self.assertEqual(projected["cells"],bad[0]["cells"])
        self.assertEqual(projected["extraction_id"],bad[0]["extraction_id"])
        self.assertEqual(data,before)

    def test_other_source_sale_is_still_a_conflict(self):
        data=literal_input()
        secondary=bind_secondary(data,"type","sale")
        result=observe_content_rows([data],document_page_count=3,secondary_observations=[secondary])
        self.assertIn("type_observation_conflict",result["slot_content"][0]["relation_evidence"]["blocking_reasons"])

    def test_secondary_literal_is_interpreted_only_after_binding(self):
        data=literal_input("?")
        secondary=bind_secondary(data,"type","purchaso")
        secondary["pages"][0]["slots"][0]["column_shape"]=None
        result=observe_content_rows([data],document_page_count=3,secondary_observations=[secondary])
        reads=result["field_observations"]["pages"][0]["slots"][0]["field_readings"]["type"]["observations"]
        self.assertEqual(reads[1]["shape"]["value"],"purchase")
        self.assertEqual(reads[1]["raw_ocr_text"],"purchaso")

    def test_compound_or_unbound_literal_is_not_interpreted(self):
        for text in ("repurchase","purchaso sale","no purchaso","purchase-related"):
            result=observe_content_rows([literal_input(text)],document_page_count=3)
            self.assertIsNone(result["field_observations"]["pages"][0]["slots"][0]["field_shapes"]["type"]["value"])
        data=literal_input();data["pages"][0]["slots"][0]["column_observation"]["type"]["word_indices"]=None
        # Existing word evidence rejects malformed mappings, rather than
        # trusting a standalone self-reported literal as source geometry.
        with self.assertRaises(ValueError):observe_content_rows([data],document_page_count=3)


if __name__=="__main__":unittest.main()
