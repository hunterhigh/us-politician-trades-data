"""Native exhibit contracts; synthetic inputs are not source accuracy gold."""
from copy import deepcopy
import hashlib
import json
import sys
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_oge_candidate import extraction, direct, catalog, base, DOCUMENT_ID, URL
from test_oge_annual import _Document,_Page
from test_oge_shadow_run import _inputs
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_278t_page_shadow import interpret_native_exhibit_page
from unison_snapshot.oge_reports import materialize_saved_oge_278t_exhibits,parse_table_rows
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.pipeline_qa.oge_run import build_oge_shadow_run,OgeShadowInputError


def observation(predecessor=None):
    label="In place of line item #27"+(" in report "+predecessor if predecessor else "")+", substitute the following:"
    words=[{"text":label,"x0":5.,"top":10.,"x1":585.,"bottom":22.}]
    headers=["#","Description","Type","Date","Notification","Amount"]
    cells=["27","ALPHA","Purchase","05/01/2025","No","$1,001 - $15,000"]
    positions=[10.,70.,250.,320.,390.,460.]
    for text,x,width in zip(headers,positions,[10,70,40,40,65,60]):
        words.append({"text":text,"x0":x,"top":40.,"x1":x+width,"bottom":50.})
    for text,x,width in zip(cells,positions,[20,50,50,65,20,100]):
        words.append({"text":text,"x0":x,"top":80.,"x1":x+width,"bottom":90.})
    text=label+"\n"+" ".join(headers)+"\n"+" ".join(cells)
    return {"schema_version":"oge-278t-native-page-observation/v1","method":"existing_embedded_pdf_text_and_table_borders_no_new_ocr",
        "source_sha256":"a"*64,"page_number":3,"document_page_count":4,"coordinate_frame":"pdfplumber_displayed_pdf_points",
        "page_width":600.,"page_height":800.,"page_text":text,"page_text_sha256":hashlib.sha256(text.encode()).hexdigest(),
        "words":words,"table_bounds":[[5.,35.,590.,110.]],"tables":[[headers,cells]]}


def empty_source():
    value=extraction();value.update(transactions=[],quarantined=[],filed_at=None,evidence_complete=False,
        document_reasons=["filer_signature_date_not_unique","transaction_table_not_found"])
    value.pop("source_rows",None)
    return value


def bundle(obs=None,targets=None):
    return {"observations":[obs or observation()],"archived_pdf_path":"synthetic-source.pdf", "replacement_targets":targets or []}


class NativeExhibitTests(unittest.TestCase):
    def test_source_words_partition_and_labels_are_complete_without_geometry_claim(self):
        obs=observation();result=interpret_native_exhibit_page(obs);row=result["blocks"][0]
        self.assertEqual(row["label"]["target_printed_number"],27)
        self.assertEqual(row["raw_columns"]["description"],"ALPHA")
        self.assertEqual(sorted(i for refs in row["field_word_indices"].values() for i in refs),row["body_word_indices"])
        self.assertEqual(row["partition_crossing_word_indices"],[])
        self.assertIn("candidate_not_verified",row["partition_basis"])
        wrong=deepcopy(obs);wrong["page_text"]=wrong["page_text"].replace("#27","#28")
        wrong["page_text_sha256"]=hashlib.sha256(wrong["page_text"].encode()).hexdigest()
        with self.assertRaisesRegex(ValueError,"label ordering"):
            interpret_native_exhibit_page(wrong)

    def test_real_candidate_and_row_ledger_consume_pending_replacement(self):
        old=empty_source();before=deepcopy(old);obs=observation()
        with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=obs):
            materialized=materialize_saved_oge_278t_exhibits(old,direct(),**bundle(obs))
            ledger=adapt_oge_278t_extraction(materialized,direct())
            candidate,audit=build_oge_candidate(catalog(),[old],base(),data_cutoff_at="2025-06-04T00:00:00Z",
                saved_exhibit_observations_by_document={DOCUMENT_ID:bundle(obs)})
        self.assertEqual(old,before)
        self.assertEqual(ledger["counts"]["quarantined"],1)
        self.assertEqual(candidate["transactions"],[])
        row=audit["reports"][0]["quarantined"][0]
        self.assertIn("replacement_target_document_unresolved",row["reasons"])
        self.assertTrue(row["native_exhibit_reference"]["field_word_indices"])
        self.assertEqual({o["method"] for o in ledger["rows"][0]["observations"]},{"embedded_text"})
        self.assertFalse(materialized["observation_receipt"]["coverage_complete"])
        self.assertEqual(materialized["observation_receipt"]["unobserved_pages"],[1,2,4])
        self.assertNotIn("cover_date_evidence",materialized)

    def test_prior_page_inventory_is_not_counted_twice_or_dropped_from_formal_base(self):
        old=empty_source();raw=["27","ALPHA","Purchase","05/01/2025","No","$1,001 - $15,000"]
        good,_=parse_table_rows([(3,raw)],source_sha=old["source_sha256"])
        old.update(transactions=good,source_rows=[{"page_number":3,"cells":raw}],filed_at="2025-06-03",
            evidence_complete=True,document_reasons=[])
        production,_=build_oge_candidate(catalog(),[old],base(),data_cutoff_at="2025-06-04T00:00:00Z")
        obs=observation()
        with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=obs):
            result=materialize_saved_oge_278t_exhibits(old,direct(),**bundle(obs))
            candidate,audit=build_oge_candidate(catalog(),[old],production,data_cutoff_at="2025-06-04T00:00:00Z",
                saved_exhibit_observations_by_document={DOCUMENT_ID:bundle(obs)})
        self.assertEqual(len(result["source_rows"]),1)
        self.assertEqual(candidate["transactions"],production["transactions"])
        receipt=audit["reports"][0]["observation_receipt"]
        self.assertEqual(receipt["unresolved_slot_count"],1)
        self.assertEqual(receipt["rows"][0]["reasons"],["native_exhibit_prior_inventory_relation_unresolved"])
        self.assertFalse(receipt["coverage_complete"])

    def test_cache_tampering_is_checked_against_the_same_original_selected_page(self):
        original=observation()
        for mutate in [lambda v:v["words"][8].update(text="FAKE"),
                lambda v:v["table_bounds"][0].__setitem__(2,589),lambda v:v.update(page_number=2)]:
            changed=deepcopy(original);mutate(changed)
            with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=original),self.assertRaises(OgeCatalogError):
                materialize_saved_oge_278t_exhibits(empty_source(),direct(),**bundle(changed))

    def test_printed_number_payload_is_not_authority_for_a_predecessor(self):
        obs=observation()
        with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=obs):
            result=materialize_saved_oge_278t_exhibits(empty_source(),direct(),**bundle(obs,[{"target_document_id":"b"*32,"target_printed_number":27}]))
        relation=result["quarantined"][0]["native_exhibit_reference"]["replacement_relation"]
        self.assertEqual(relation["status"],"pending")
        self.assertNotIn("target",relation)

    def test_explicit_source_url_original_archive_and_unique_raw_row_bind_target(self):
        obs=observation(URL);raw=["27","OLD ALPHA","Purchase","05/01/2025","No","$1,001 - $15,000"]
        content=b"%PDF-1.7\nsynthetic previous source\n%%EOF";sha=hashlib.sha256(content).hexdigest()
        good,_=parse_table_rows([(1,raw)],source_sha=sha)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/f"oge/reports/{DOCUMENT_ID}/{sha}.pdf";source.parent.mkdir(parents=True);source.write_bytes(content)
            meta={"schema_version":"oge-278t-archive/v1","source_id":"oge","document_id":DOCUMENT_ID,"sha256":sha,
                "byte_length":len(content),"archive_path":source.relative_to(root).as_posix(),"document_url":URL,"filer_name":"Example, Ada"}
            meta_path=source.with_suffix(".json");meta_path.write_text(json.dumps(meta),encoding="utf-8")
            target=extraction();target.update(source_sha256=sha,transactions=good,quarantined=[])
            targets=[{"evidence_root":str(root),"archive_metadata_path":str(meta_path),"extraction":target}]
            with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=obs),patch(
                    "pdfplumber.open",return_value=_Document([_Page("",[raw])])),patch(
                    "unison_snapshot.oge_reports._extract_borderless_transaction_tables",return_value=[]):
                result=materialize_saved_oge_278t_exhibits(empty_source(),direct(),**bundle(obs,targets))
            relation=result["quarantined"][0]["native_exhibit_reference"]["replacement_relation"]
            self.assertEqual(relation["status"],"bound_replacement_target")
            self.assertEqual(relation["target"]["target_source_sha256"],sha)
            self.assertEqual(relation["target"]["target_extraction_id"],good[0]["extraction_id"])
            self.assertIn("replacement_application_requires_source_reconciliation",result["quarantined"][0]["reasons"])
            meta["sha256"]="f"*64;meta_path.write_text(json.dumps(meta),encoding="utf-8")
            with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=obs),self.assertRaisesRegex(OgeCatalogError,"archive metadata"):
                materialize_saved_oge_278t_exhibits(empty_source(),direct(),**bundle(obs,targets))

    def test_actual_runner_candidate_and_final_builder_keep_unknown_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);kwargs=_inputs(root)
            old=empty_source();obs=observation()
            for file in kwargs["extractions_dir"].glob("*.json"):file.unlink()
            (kwargs["extractions_dir"]/f"{DOCUMENT_ID}.json").write_text(json.dumps(old),encoding="utf-8")
            kwargs["catalog_path"].write_text(json.dumps(catalog()),encoding="utf-8")
            archive=json.loads(kwargs["archive_batch_path"].read_text(encoding="utf-8"));archive["reports"][0].update(
                document_id=DOCUMENT_ID,document_url=URL,sha256="a"*64,archive_path=f"oge/reports/{DOCUMENT_ID}/{'a'*64}.pdf",
                filer_name="Example, Ada",agency="Example Agency",position_title="Director",catalog_added_date="2025-06-04",amended_label=None)
            kwargs["archive_batch_path"].write_text(json.dumps(archive),encoding="utf-8")
            inputs=root/"exhibit.json";inputs.write_text(json.dumps({DOCUMENT_ID:bundle(obs)}),encoding="utf-8")
            base_file=root/"base.json";base_file.write_text(json.dumps(base()),encoding="utf-8")
            from unison_snapshot.pipeline_qa.oge_run_cli import main as run_cli
            args=["--catalog",str(kwargs["catalog_path"]),"--archive-batch",str(kwargs["archive_batch_path"]),
                "--extraction-batch",str(kwargs["extraction_batch_path"]),"--extractions-dir",str(kwargs["extractions_dir"]),
                "--output-dir",str(kwargs["output_dir"]),"--run-id",kwargs["run_id"],"--code-commit",kwargs["code_commit"],
                "--evidence-commit",kwargs["evidence_commit"],"--started-at",kwargs["started_at"],"--completed-at",kwargs["completed_at"],
                "--saved-exhibit-observations",str(inputs),"--candidate-base",str(base_file),"--data-cutoff-at","2025-06-04T00:00:00Z"]
            with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=obs),redirect_stdout(io.StringIO()):
                self.assertEqual(run_cli(args),0)
            from unison_snapshot.__main__ import main as candidate_cli
            candidate_args=["unison_snapshot","build-oge-candidate","--catalog",str(kwargs["catalog_path"]),
                "--extractions-dir",str(kwargs["extractions_dir"]),"--base",str(base_file),"--data-cutoff-at","2025-06-04T00:00:00Z",
                "--output",str(root/"direct-candidate.json"),"--audit-output",str(root/"direct-audit.json"),
                "--saved-exhibit-observations",str(inputs)]
            with patch("unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page",return_value=obs),patch.object(sys,"argv",candidate_args),redirect_stdout(io.StringIO()):
                candidate_cli()
            direct_audit=json.loads((root/"direct-audit.json").read_text(encoding="utf-8"))
            self.assertEqual(direct_audit["reports"][0]["observation_receipt"]["mode"],"native_exhibit_substitution")
            ledger=json.loads((kwargs["output_dir"]/"candidate_rows.json").read_text(encoding="utf-8"))
            audit=json.loads((kwargs["output_dir"]/"candidate_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ledger),1)
            self.assertEqual(ledger[0]["disposition"],"quarantined")
            self.assertEqual(audit["reports"][0]["observation_receipt"]["unobserved_pages"],[1,2,4])
            self.assertTrue((kwargs["output_dir"]/"snapshot/manifest.json").is_file())

if __name__=="__main__":unittest.main()
