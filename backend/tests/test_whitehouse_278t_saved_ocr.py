"""Saved-word consumer contracts; synthetic geometry is not source OCR gold."""
from copy import deepcopy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from test_oge_content_relations import saved
from test_whitehouse_278t_generic import COVER, consume
from unison_snapshot.whitehouse_278t import (materialize_saved_whitehouse_278t,
    validate_generic_whitehouse_report, saved_ocr_source_roots,
    SAVED_OCR_PARSER_VERSION, OCR_SHARD_SCHEMA)
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.builder import build


class SavedWhiteHouseTests(unittest.TestCase):
    def test_labelled_identity_table_is_unknown_not_a_header_named_person(self):
        from unison_snapshot.whitehouse_278t import _generic_cover_identity
        for body in ['Position | Agency\nLast Name First Name\nTrump Donaid\nPresident\n',
                     'Last Name First Name\nPosition Agency\n', '',
                     'Filer\'s Certification\nDate: 06/13/2026\n']:
            cover="Filer's Information\n"+body+"Filer's Certification\nDate: 06/13/2026\n"
            value=_generic_cover_identity(cover)
            self.assertIsNone(value[0]);self.assertIn('pdf_filer_information_not_verified',value[-1])
        self.assertEqual(_generic_cover_identity("Filer's Information\nWiles, Susie\nChief of Staff - White House\nFiler's Certification\n")[0],'Wiles, Susie')

    def test_explicit_native_body_interface_validates_source_and_preserves_consumer(self):
        with tempfile.TemporaryDirectory() as folder:
            inputs, geometry = self.fixture(Path(folder))
            inputs.update(geometry_mode="source_pdf_dual_axis/v1",
                          word_observation_mode="source_objects_with_ocr_header/v1",
                          observation_resolution_dpi=300)
            path = Path(inputs["checkpoint_path"])
            checkpoint = json.loads(path.read_bytes())
            body = checkpoint["pages"][1]
            body["render_frame"] = {"resolution":300}
            body["words"][0]["source_object_index"] = 12
            for key in ("block_num", "par_num", "line_num"):
                body["words"][0].pop(key)
            path.write_text(json.dumps(checkpoint))
            inputs["checkpoint_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.context(geometry), patch("unison_snapshot.pdf_text_object_words.validate_saved_native_words",
                    return_value={"native_word_count":1}) as validator:
                result = materialize_saved_whitehouse_278t(inputs)
                candidate, _ = consume(result)
            self.assertEqual(len(candidate["transactions"]),1)
            self.assertEqual(validator.call_args.kwargs["page_number"],2)
            self.assertEqual(validator.call_args.kwargs["words"],body["words"])
            self.assertEqual(result["observation_receipt"]["observation_resolution_dpi"],300)
            with self.context(geometry), patch("unison_snapshot.pdf_text_object_words.validate_saved_native_words",
                    side_effect=ValueError("original text object")):
                with self.assertRaises(OgeCatalogError):
                    materialize_saved_whitehouse_278t(inputs)
            inputs.pop("word_observation_mode")
            with self.context(geometry), self.assertRaises(OgeCatalogError):
                materialize_saved_whitehouse_278t(inputs)

    def fixture(self, root, *, original=True):
        pdf = b"%PDF-1.7\nsynthetic saved-word contract\n%%EOF"
        sha = hashlib.sha256(pdf).hexdigest()
        url = "https://www.whitehouse.gov/wp-content/uploads/2026/06/Wiles-PTR.pdf"
        doc = "wh-url:" + hashlib.sha256(url.encode()).hexdigest()[:24]
        geometry = saved([["", "Investment account #1", "", "", "", ""],
                          ["1", "ALPHA", "purchase", "06/01/2026", "No", "$1,001 - $15,000"]])
        geometry["source_sha256"] = sha
        def word(text, line, box):
            return {"text":text,"x0":box[0],"top":box[1],"x1":box[2],"bottom":box[3],
                    "ocr_confidence":95,"block_num":1,"par_num":1,"line_num":line}
        cover = [word(line,n,[10,10+n*10,700,18+n*10]) for n,line in enumerate(COVER.splitlines(),1)]
        body = [word(w["text"],n,w["bbox_pdf_points"]) for n,slot in enumerate(geometry["pages"][0]["slots"],1) for w in slot["words"]]
        checkpoint = {"schema_version":OCR_SHARD_SCHEMA,"parser_version":"whitehouse-278t-hybrid-geometry/v4",
            "source_url":url,"source_sha256":sha,"ocr_engine":"synthetic-saved","page_start":1,"page_end":2,
            "pages":[{"page_number":1,"width":792,"height":612,"words":cover},
                     {"page_number":2,"width":792,"height":612,"words":body}]}
        metadata = {"schema_version":"whitehouse-public-disclosures-pdf/v1","document_id":doc,
            "sha256":sha,"document_url":url,"document_type_from_label":"278t","byte_length":len(pdf),
            "archive_path":f"whitehouse/disclosures/reports/{doc[7:]}/{sha}.pdf"}
        inputs = {"filer_name":"Wiles, Susie","selected_page_numbers":[1,2],"document_page_count":3}
        for name,value in (("checkpoint",checkpoint),("archive_metadata",metadata)):
            path=root/(name+".json");raw=json.dumps(value).encode();path.write_bytes(raw)
            inputs[name+"_path"]=str(path);inputs[name+"_sha256"]=hashlib.sha256(raw).hexdigest()
        if original:
            path=root/"source.pdf";path.write_bytes(pdf);inputs["archived_pdf_path"]=str(path)
        return inputs,geometry

    def context(self, geometry):
        stack=ExitStack()
        from test_oge_annual import _Document
        stack.enter_context(patch("pdfplumber.open",return_value=_Document([None]*3)))
        stack.enter_context(patch("unison_snapshot.oge_278t_raster_slots.observe_raster_slots",return_value=geometry))
        stack.enter_context(patch("unison_snapshot.whitehouse_278t.ocr_pdf_pages",side_effect=AssertionError("new OCR forbidden")))
        return stack

    def test_complete_saved_raw_reaches_existing_qualification_candidate_and_builder(self):
        with tempfile.TemporaryDirectory() as folder:
            inputs,geometry=self.fixture(Path(folder))
            with self.context(geometry):
                report=materialize_saved_whitehouse_278t(inputs)
                self.assertEqual(report["parser_version"],SAVED_OCR_PARSER_VERSION)
                self.assertEqual(report["filed_at"],"2026-06-13")
                validate_generic_whitehouse_report(report)
                candidate,audit=consume(report)
            self.assertEqual(len(candidate["transactions"]),1)
            self.assertEqual(candidate["transactions"][0]["asset_name"],"ALPHA — Investment account #1")
            self.assertEqual(candidate["transactions"][0]["owner"],"Undisclosed")
            self.assertEqual(audit["reports"][0]["observation_receipt"]["unobserved_pages"],[3])
            self.assertTrue(build(candidate,generated_at="2026-06-20T00:00:00Z",allow_production=True).manifest["snapshot_id"])

    def test_missing_original_retains_full_raw_and_unknown_not_zero_or_legacy_quarantine(self):
        with tempfile.TemporaryDirectory() as folder:
            inputs,_=self.fixture(Path(folder),original=False)
            report=materialize_saved_whitehouse_278t(inputs)
            candidate,audit=consume(report)
            self.assertEqual(report["transactions"],[])
            self.assertEqual(report["source_rows"],[])
            self.assertEqual(len(report["raw_page_observations"]),2)
            receipt=report["observation_receipt"]
            self.assertFalse(receipt["source_original_verified"])
            self.assertEqual(receipt["geometry_unknown_pages"],[2])
            self.assertIsNone(receipt["unique_source_row_count"])
            self.assertEqual(candidate["transactions"],[])
            self.assertEqual(audit["reports"][0]["quarantined_count"],0)
            self.assertEqual(audit["reports"][0]["observation_receipt"],receipt)

    def test_tampered_source_refs_pages_and_report_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            inputs,geometry=self.fixture(Path(folder))
            with self.context(geometry):
                report=materialize_saved_whitehouse_278t(inputs)
                for field,value in (("raw_page_observations",[]),("observation_receipt",{}),("source_rows",[])):
                    changed=deepcopy(report);changed[field]=value
                    with self.assertRaisesRegex(OgeCatalogError,"source-input replay"):
                        validate_generic_whitehouse_report(changed)
            for value in ([2],[1,1],[1,3]):
                changed=deepcopy(inputs);changed["selected_page_numbers"]=value
                with self.assertRaises(OgeCatalogError):materialize_saved_whitehouse_278t(changed)
            Path(inputs["checkpoint_path"]).write_text("{}",encoding="utf-8")
            with self.assertRaisesRegex(OgeCatalogError,"file bytes differ"):
                materialize_saved_whitehouse_278t(inputs)

    def test_old_default_coverage_selection_does_not_implicitly_enable_new_cache(self):
        import sys
        sys.path.insert(0,str(Path(__file__).parents[1]/"scripts"))
        from whitehouse_coverage import _trade_versions
        self.assertNotIn(SAVED_OCR_PARSER_VERSION,_trade_versions("https://www.whitehouse.gov/x.pdf","a"*64))
        self.assertEqual(_trade_versions("https://www.whitehouse.gov/x.pdf","a"*64,saved_ocr_trade=True),(SAVED_OCR_PARSER_VERSION,))

    def test_relative_source_refs_replay_after_directory_move_and_reject_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"source";source.mkdir()
            inputs,_=self.fixture(source,original=False)
            for key in ("archive_metadata_path","checkpoint_path"):
                inputs[key]=Path(inputs[key]).name
            with self.assertRaisesRegex(OgeCatalogError,"explicit evidence/review roots"):
                materialize_saved_whitehouse_278t(inputs)
            with saved_ocr_source_roots(source,source):
                report=materialize_saved_whitehouse_278t(inputs)
            target=root/"copied";shutil.copytree(source,target)
            shutil.rmtree(source)
            with saved_ocr_source_roots(target,target):
                self.assertEqual(materialize_saved_whitehouse_278t(inputs),report)
                validate_generic_whitehouse_report(report)
                altered=deepcopy(inputs);altered["checkpoint_path"]="../outside.json"
                with self.assertRaisesRegex(OgeCatalogError,"escapes controlled root"):
                    materialize_saved_whitehouse_278t(altered)
            self.assertEqual(report["observation_receipt"]["input_reference_mode"],"controlled_relative_roots")

    def test_shared_saved_words_do_not_call_ocr_and_reject_unproven_coordinate_frame(self):
        from types import SimpleNamespace
        from test_oge_annual import _Document
        from unison_snapshot.ocr_geometry import OcrPage
        from unison_snapshot.oge_278t_raster_slots import observe_raster_slots
        census={"results":[{"page_number":1,"status":"coverage_unknown",
            "column_geometry_observation":{"status":"unknown","reason":"synthetic_unknown"}}]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"source.pdf";path.write_bytes(b"same synthetic source")
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            page=SimpleNamespace(width=792,height=612,cropbox=(0,0,792,612),mediabox=(0,0,792,612))
            words=OcrPage(width=792,height=612,words=[])
            with patch("unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback",return_value=(census,{})), \
                 patch("pdfplumber.open",return_value=_Document([page])), \
                 patch("unison_snapshot.oge_278t_raster_slots.ocr_pdf_pages",side_effect=AssertionError("new OCR forbidden")):
                result=observe_raster_slots(path,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:words},saved_ocr_engine="saved")
                self.assertIsNone(result["pages"][0]["physical_slot_count"])
                census["results"][0].update(status="physical_slots_located",physical_row_bands=[[100,120]],
                    column_geometry_observation={"status":"confirmed","edges_pdf_points":[100,140,450,500,550,600,700]})
                words=OcrPage(width=792,height=612,words=[{"text":"RAW ASSET","x0":150,"top":103,"x1":220,"bottom":115,"ocr_confidence":91}])
                with patch("unison_snapshot.oge_278t_raster_slots.census_with_2024_fallback",return_value=(census,{1:[100,140,450,500,550,600,700]})):
                    result=observe_raster_slots(path,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:words},saved_ocr_engine="saved")
                self.assertEqual(result["ocr_engine"],"saved")
                self.assertEqual(result["pages"][0]["slots"][0]["raw_ocr_text"],"RAW ASSET")
                for bad in (OcrPage(width=612,height=792,words=[]),):
                    with self.assertRaisesRegex(ValueError,"coordinate frame"):
                        observe_raster_slots(path,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:bad},saved_ocr_engine="saved")
                page.cropbox=(1,0,792,612)
                with self.assertRaisesRegex(ValueError,"coordinate frame"):
                    observe_raster_slots(path,expected_sha256=sha,page_numbers=[1],saved_ocr_pages={1:words},saved_ocr_engine="saved")

    def test_existing_extraction_entry_requires_explicit_source_and_writes_v6_only(self):
        from scripts.whitehouse_extract import extract_batch
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);inputs,_=self.fixture(root,original=False)
            reference=root/"input.json";reference.write_text(json.dumps(inputs),encoding="utf-8")
            metadata=json.loads(Path(inputs["archive_metadata_path"]).read_bytes())
            review=root/"review"
            kwargs={"saved_ocr_input":reference,"document_id":metadata["document_id"],"source_sha256":metadata["sha256"]}
            with self.assertRaisesRegex(ValueError,"one explicit source"):
                extract_batch(root,review,limit=2,**kwargs)
            with self.assertRaisesRegex(ValueError,"one explicit source"):
                extract_batch(root,review,limit=1,generic_trade=True,**kwargs)
            with patch("unison_snapshot.whitehouse_278t.ocr_pdf_pages",side_effect=AssertionError("new OCR forbidden")):
                status=extract_batch(root,review,limit=1,**kwargs)
            self.assertEqual(status["extraction_created_count"],1)
            files=list(review.glob("whitehouse/extractions/*/*/*.json"))
            self.assertEqual(len(files),1)
            self.assertEqual(files[0].stem,SAVED_OCR_PARSER_VERSION.replace("/","-"))
            self.assertFalse(json.loads(files[0].read_bytes())["observation_receipt"]["source_original_verified"])


if __name__=="__main__":unittest.main()
