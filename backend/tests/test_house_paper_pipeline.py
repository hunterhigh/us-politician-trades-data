import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from unison_snapshot.house_paper_pipeline import bridge_paper_batch, bridge_paper_report
from unison_snapshot.house_ptr_paper_temporal import PARSER_VERSION


class HousePaperPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.evidence = root / "evidence"
        self.review = root / "review"
        self.pdf = b"archived paper"
        self.sha = hashlib.sha256(self.pdf).hexdigest()
        self.relative = Path("2025/9116141") / f"{self.sha}.json"
        self.metadata_path = self.evidence / "house_clerk/documents" / self.relative
        self.metadata = {
            "source_id": "house_clerk", "filing_type": "P", "filing_year": 2025,
            "document_id": "9116141", "sha256": self.sha,
            "filer_name": "Example, Person", "state_district": "CA01",
            "filed_date": "2025-01-02",
            "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2025/9116141.pdf",
            "archive_path": "house_clerk/archive/9116141.pdf",
        }
        self._write(self.metadata_path, self.metadata)
        pdf_path = self.evidence / self.metadata["archive_path"]
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(self.pdf)
        self._write(self.review / "house_clerk/extractions" / self.relative,
                    {"extraction_method": "tesseract_legacy_checkbox"})
        self._write(self.review / "house_clerk/identities" / self.relative,
                    {"document_id": "9116141", "person_id": "house:example"})
        self.legacy = {"document_id": "9116141", "source_sha256": self.sha,
                       "transactions": [], "quarantined": [{"old": True}]}
        self.qualification_path = self.review / "house_clerk/qualifications" / self.relative
        self._write(self.qualification_path, self.legacy)
        self.observation = {"source_sha256": self.sha, "pages": []}
        self.header = {"source_sha256": self.sha, "pages": []}
        self.paper = {"parser_version": PARSER_VERSION, "transactions": [],
                      "source_sha256": self.sha, "document_id": "9116141"}

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def _secondary_fixture(self):
        from test_house_ptr_key_fields_shadow import report
        self.observation = report()
        self.observation["source_sha256"] = self.sha
        page = self.observation["pages"][0]
        page.update(coordinate_frame="original_page", orientation_degrees=0)
        cell = page["rows"][0]["cells"]["asset"]
        cell.update(bbox_normalized=[.1,.1,.9,.3], crop_sha256="a"*64)
        return {"schema_version":"house-paper-key-field-reread/v1", "source_sha256":self.sha,
            "rule_version":"house-paper-key-field-conflict-probe-v2",
            "qualification_status":"unverified_shadow_only", "selected_cell_count":1,
            "rereads":[{"page":1,"physical_band_index":1,"field":"asset",
                "first_ocr_raw":cell["raw_text"], "first_crop_sha256":cell["crop_sha256"],
                "second_ocr_raw":cell["raw_text"], "second_crop_sha256":"b"*64,
                "second_crop_render_dpi":300}]}

    def test_qualified_legacy_rows_are_preserved_without_ocr(self):
        self.legacy["transactions"] = [{"id": "house:old"}]
        self._write(self.qualification_path, self.legacy)
        with patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf") as observe:
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
        self.assertEqual(outcome, "legacy_qualified_preserved")
        observe.assert_not_called()
        self.assertEqual(json.loads(self.qualification_path.read_text()), self.legacy)

    def test_new_observation_replaces_only_empty_legacy_qualification(self):
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                    return_value=self.observation),
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header",
                    return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                    return_value=self.paper)):
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
        self.assertEqual(outcome, "observed_and_qualified")
        self.assertEqual(json.loads(self.qualification_path.read_text()), self.paper)
        self.assertTrue((self.review / "house_clerk/paper_observations" / self.relative).is_file())
        self.assertTrue((self.review / "house_clerk/paper_amount_headers" / self.relative).is_file())
        self.assertEqual(json.loads((self.review / "house_clerk/extractions" /
                                     self.relative).read_text())["extraction_method"],
                         "tesseract_legacy_checkbox")

    def test_opt_in_saved_materialization_cannot_start_missing_ocr(self):
        self._write(self.review / "house_clerk/paper_observations" / self.relative, self.observation)
        self._write(self.review / "house_clerk/paper_amount_headers" / self.relative, self.header)
        paper = dict(self.paper, quarantined=[{"source_quality_flags": ["asset_ocr_confidence_below_70"]}])
        materialized = dict(self.paper, parser_version="house-paper-source-bound-qualification-v2",
            materialized_extraction={"source_sha256": self.sha, "source": self.metadata,
                                    "extraction_method": "source_bound_paper_cells"})
        with (patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation", return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.preserved_paper_materialization", return_value=True),
              patch("unison_snapshot.house_paper_pipeline.materialize_paper_observation", return_value=materialized) as materialize,
              patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf") as observe,
              patch("unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells") as reread):
            outcome = bridge_paper_report(evidence_root=self.evidence, review_root=self.review,
                metadata_path=self.metadata_path, tesseract="unused", observe_new=False,
                materialize_saved=True)
        self.assertEqual(outcome, "materialized_existing_observation")
        observe.assert_not_called()
        reread.assert_not_called()
        self.assertIsNone(materialize.call_args.kwargs["field_reread"])
        actual = json.loads(self.qualification_path.read_text())
        self.assertEqual(actual, materialized)

    def test_saved_failed_parser_input_needs_no_fabricated_legacy_extraction(self):
        (self.review / "house_clerk/extractions" / self.relative).unlink()
        self.qualification_path.unlink()
        self._write(self.review / "house_clerk/failures" / self.relative,
            {"schema_version": "house-ptr-parse-failure/v1", "document_id": "9116141",
             "source_sha256": self.sha})
        self._write(self.review / "house_clerk/paper_observations" / self.relative, self.observation)
        self._write(self.review / "house_clerk/paper_amount_headers" / self.relative, self.header)
        with (patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation", return_value=self.paper),
              patch("unison_snapshot.house_paper_pipeline.preserved_paper_materialization", return_value=True),
              patch("unison_snapshot.house_paper_pipeline.materialize_paper_observation", return_value={
                  **self.paper, "materialized_extraction": {"source_sha256": self.sha,
                    "source": self.metadata, "extraction_method": "source_bound_paper_cells"}}) as materialize):
            outcome = bridge_paper_report(evidence_root=self.evidence, review_root=self.review,
                metadata_path=self.metadata_path, tesseract="unused", observe_new=False,
                materialize_saved=True)
        self.assertEqual(outcome, "materialized_existing_observation")
        self.assertIsNone(materialize.call_args.kwargs["prior_qualification"])
        generated = json.loads((self.review / "house_clerk/extractions" / self.relative).read_text())
        self.assertEqual(generated["extraction_method"], "source_bound_paper_cells")

    def test_failed_observation_keeps_legacy_and_records_failure(self):
        batch = Path(self.temporary.name) / "batch.txt"
        batch.write_text("", encoding="utf-8")
        with patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                   side_effect=ValueError("unsupported grid")):
            result = bridge_paper_batch(evidence_root=self.evidence,
                                        review_root=self.review, parse_batch=batch,
                                        tesseract="tesseract", document_id="9116141")
        self.assertEqual(result, {"paper_observation_failed": 1})
        self.assertEqual(json.loads(self.qualification_path.read_text()), self.legacy)
        failure = self.review / "house_clerk/paper_failures" / self.relative
        self.assertIn("unsupported grid", failure.read_text())

    def test_same_filer_official_peer_is_recorded_without_promotion(self):
        peer = dict(self.metadata, document_id="9116142", filed_date="2025-02-03")
        peer["source_url"] = peer["source_url"].replace("9116141.pdf", "9116142.pdf")
        self._write(self.evidence / "house_clerk/documents/2025/9116142" /
                    f"{self.sha}.json", peer)
        other = dict(peer, document_id="9116143", filer_name="Other, Person")
        other["source_url"] = other["source_url"].replace("9116142.pdf", "9116143.pdf")
        self._write(self.evidence / "house_clerk/documents/2025/9116143" /
                    f"{self.sha}.json", other)
        paper = dict(self.paper, quarantined=[{
            "raw_fields": {"event_date": "2025-01-20"}}])
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                    return_value=self.observation),
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header",
                    return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                    return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.audit_paper_temporal_relations",
                    return_value={"status": "read_only"}) as audit):
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
        self.assertEqual(outcome, "observed_and_qualified")
        self.assertEqual(json.loads(self.qualification_path.read_text())
                         ["temporal_relation_observation"], {"status": "read_only"})
        self.assertEqual([value["document_id"] for value in audit.call_args.args[2]],
                         ["9116142"])

    def test_doubtful_key_cell_reread_is_saved_and_reused(self):
        reread = self._secondary_fixture()
        paper = dict(self.paper, quarantined=[{"source_quality_flags": [
            "asset_ocr_confidence_below_70"]}])
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                    return_value=self.observation),
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header",
                    return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                    return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells",
                    return_value=reread) as reread_fn):
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
            replay = bridge_paper_report(evidence_root=self.evidence,
                                         review_root=self.review,
                                         metadata_path=self.metadata_path,
                                         tesseract="tesseract", observe_new=False)
        self.assertEqual((outcome, replay),
                         ("observed_and_qualified", "requalified_existing_observation"))
        reread_fn.assert_called_once()
        self.assertEqual(json.loads(self.qualification_path.read_text())
                         ["key_field_reread_status"], "observed")
        self.assertEqual(json.loads((self.review / "house_clerk/paper_key_field_rereads" /
                                     self.relative).read_text()), reread)

    def test_older_reread_is_refreshed_without_reobserving_paper(self):
        new_reread = self._secondary_fixture()
        paper = dict(self.paper, quarantined=[{"source_quality_flags": [
            "asset_ocr_confidence_below_70"]}])
        self._write(self.review / "house_clerk/paper_observations" / self.relative,
                    self.observation)
        self._write(self.review / "house_clerk/paper_amount_headers" / self.relative,
                    self.header)
        old_path = self.review / "house_clerk/paper_key_field_rereads" / self.relative
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text(json.dumps({"source_sha256": self.sha,
                                        "qualification_status": "unverified_shadow_only"}))
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                    return_value=self.observation) as observe,
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header",
                    return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                    return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells",
                    return_value=new_reread) as reread_fn):
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=False)
        self.assertEqual(outcome, "requalified_existing_observation")
        observe.assert_not_called()
        reread_fn.assert_called_once()
        self.assertEqual(json.loads(old_path.read_text()), new_reread)

    def test_failed_second_read_keeps_paper_rows_quarantined(self):
        self._secondary_fixture()
        paper = dict(self.paper, quarantined=[{"source_quality_flags": [
            "event_date_ocr_confidence_below_70"]}])
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                    return_value=self.observation),
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header",
                    return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                    return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells",
                    side_effect=ValueError("crop unreadable"))):
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
        self.assertEqual(outcome, "observed_and_qualified")
        qualification = json.loads(self.qualification_path.read_text())
        self.assertEqual(qualification["transactions"], [])
        self.assertEqual(qualification["key_field_reread_status"], "unresolved")
        self.assertIn("crop unreadable", (self.review / "house_clerk/paper_reread_failures" /
                                          self.relative).read_text())

    def test_high_confidence_assets_trigger_required_preparation_and_incomplete_cache_is_completed(self):
        from unison_snapshot.house_ptr_key_field_reread import MATERIALIZATION_SELECTION
        complete = self._secondary_fixture()
        paper = dict(self.paper, quarantined=[{"source_quality_flags": []}])
        partial = dict(complete, selected_cell_count=0, rereads=[])
        cache_path = self.review / "house_clerk/paper_key_field_rereads" / self.relative
        self._write(cache_path, partial)
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf", return_value=self.observation),
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header", return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation", return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells", return_value=complete) as prepare):
            bridge_paper_report(evidence_root=self.evidence, review_root=self.review,
                metadata_path=self.metadata_path, tesseract="fixture", observe_new=True)
        prepare.assert_called_once()
        self.assertEqual(prepare.call_args.kwargs["selection_mode"], MATERIALIZATION_SELECTION)
        self.assertEqual(prepare.call_args.kwargs["existing_reread"], partial)
        self.assertEqual(json.loads(cache_path.read_bytes()), complete)

    def test_saved_replay_retains_partial_cache_and_never_fills_it_with_ocr(self):
        complete = self._secondary_fixture()
        partial = dict(complete, selected_cell_count=0, rereads=[])
        self._write(self.review / "house_clerk/paper_observations" / self.relative, self.observation)
        self._write(self.review / "house_clerk/paper_amount_headers" / self.relative, self.header)
        cache_path = self.review / "house_clerk/paper_key_field_rereads" / self.relative
        self._write(cache_path, partial)
        before = cache_path.read_bytes()
        paper = dict(self.paper, quarantined=[{"source_quality_flags": []}],
            materialized_extraction={"source_sha256":self.sha, "source":self.metadata,
                                    "extraction_method":"source_bound_paper_cells"})
        with (patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation", return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.preserved_paper_materialization", return_value=True),
              patch("unison_snapshot.house_paper_pipeline.materialize_paper_observation", return_value=paper) as materialize,
              patch("unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells",
                    side_effect=AssertionError("saved replay cannot OCR"))):
            bridge_paper_report(evidence_root=self.evidence, review_root=self.review,
                metadata_path=self.metadata_path, tesseract="must not run", observe_new=False, materialize_saved=True)
        self.assertEqual(materialize.call_args.kwargs["field_reread"], partial)
        self.assertEqual(cache_path.read_bytes(), before)
        self.assertIn("lacks 1 required secondary readings", (self.review /
            "house_clerk/paper_reread_failures" / self.relative).read_text())

    def test_saved_replay_cannot_revive_a_wrong_source_cache(self):
        cached = self._secondary_fixture()
        cached["source_sha256"] = "c" * 64
        self._write(self.review / "house_clerk/paper_observations" / self.relative, self.observation)
        self._write(self.review / "house_clerk/paper_amount_headers" / self.relative, self.header)
        self._write(self.review / "house_clerk/paper_key_field_rereads" / self.relative, cached)
        paper = dict(self.paper, materialized_extraction={"source_sha256":self.sha,
            "source":self.metadata,"extraction_method":"source_bound_paper_cells"})
        with (patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",return_value=paper),
              patch("unison_snapshot.house_paper_pipeline.preserved_paper_materialization",return_value=True),
              patch("unison_snapshot.house_paper_pipeline.materialize_paper_observation",return_value=paper) as materialize,
              patch("unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells",
                    side_effect=AssertionError("saved replay cannot OCR"))):
            bridge_paper_report(evidence_root=self.evidence,review_root=self.review,
                metadata_path=self.metadata_path,tesseract="unused",observe_new=False,materialize_saved=True)
        self.assertIsNone(materialize.call_args.kwargs["field_reread"])

    def test_temporal_hint_failure_does_not_block_paper_qualification(self):
        peer = dict(self.metadata, document_id="9116142", filed_date="invalid")
        peer["source_url"] = peer["source_url"].replace("9116141.pdf", "9116142.pdf")
        self._write(self.evidence / "house_clerk/documents/2025/9116142" /
                    f"{self.sha}.json", peer)
        paper = dict(self.paper, quarantined=[{
            "raw_fields": {"event_date": "2025-01-20"}}])
        with (patch("unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf",
                    return_value=self.observation),
              patch("unison_snapshot.house_paper_pipeline.observe_amount_header",
                    return_value=self.header),
              patch("unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                    return_value=paper)):
            outcome = bridge_paper_report(evidence_root=self.evidence,
                                          review_root=self.review,
                                          metadata_path=self.metadata_path,
                                          tesseract="tesseract", observe_new=True)
        self.assertEqual(outcome, "observed_and_qualified")
        self.assertEqual(json.loads(self.qualification_path.read_text())["transactions"], [])
        self.assertIn("filing date is invalid", (self.review /
                      "house_clerk/paper_temporal_failures" / self.relative).read_text())


if __name__ == "__main__":
    unittest.main()
