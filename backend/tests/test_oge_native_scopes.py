"""Constructed native extraction/consumer contracts, not official source gold."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_oge_reports import record, Client, PDF
from test_oge_candidate import base
from unison_snapshot.oge import OgeSourceConfig, SCHEMA
from unison_snapshot.oge_reports import archive_direct_pdf, parse_archived_pdf
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction
from unison_snapshot.builder import build

ROW=["1","Example Fund (EXM)","Purchase","06/01/2026","No","$1,001 - $15,000"]
def label(value):return ["",value,"","","",""]

class NativeScopeTests(unittest.TestCase):
    def source(self, inventory, generic=True):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);meta=archive_direct_pdf(root,record(),OgeSourceConfig(True,True),client=Client(),retrieved_at="2026-09-20T00:00:00Z")
            path=root/"oge/reports"/meta["document_id"]/f"{meta['sha256']}.json"
            cover={"text":"Filer's Certification\nDate: 06/02/2026","document_page_count":max(p for p,_ in inventory)}
            with patch("unison_snapshot.oge_reports._extract_pdf",return_value=("Electronic Signature electronically signed on 06/02/2026",inventory,inventory,cover)):
                return parse_archived_pdf(root,path,generic=generic)

    def candidate(self, source, prior=None, legacy=None):
        return build_oge_candidate({"schema_version":SCHEMA,"transactions":[record()]},[source],prior or base(),
            data_cutoff_at="2026-09-20T00:00:00Z",legacy_extractions_by_document=legacy)

    def test_native_same_asset_printed_number_different_accounts_reaches_builder(self):
        source=self.source([(2,label("Investment account #1")),(2,ROW),(2,label("Investment account #2")),(2,ROW)])
        self.assertEqual(len(source["transactions"]),2)
        result,audit=self.candidate(source)
        self.assertEqual(len(result["transactions"]),2)
        self.assertEqual({r["asset_name"] for r in result["transactions"]},{"Example Fund — Investment account #1","Example Fund — Investment account #2"})
        self.assertEqual(len({r["id"] for r in result["transactions"]}),2)
        self.assertEqual(adapt_oge_278t_extraction(source,record())["counts"]["qualified"],2)
        self.assertTrue(build(result,generated_at="2026-09-20T00:00:00Z",allow_production=True).manifest["snapshot_id"])

    def test_account_only_barrier_can_reestablish_account_without_guessing_owner(self):
        from unison_snapshot.oge_reports import parse_generic_table_rows
        from unison_snapshot.whitehouse_278t import _generic_table_rows
        rows=[(2,label("Investment account #1")),(2,ROW),(2,label("Investment account #2")),(2,ROW)]
        for consume in (parse_generic_table_rows,_generic_table_rows):
            good,bad,_=consume(rows,source_sha256="a"*64,scope_barriers_before={2:True})
            self.assertEqual(bad,[]);self.assertEqual(len(good),2)
            self.assertEqual({r["owner"] for r in good},{"Undisclosed"})
            owned=[(2,label("Spouse assets")),*rows]
            good,bad,_=consume(owned,source_sha256="a"*64,scope_barriers_before={3:True})
            self.assertEqual(len(good),1);self.assertIn("owner_context_unresolved",bad[0]["reasons"])

    def test_explicit_owner_scopes_allow_same_printed_row_and_same_title(self):
        for title in (None,"Investment account #1"):
            rows=[]
            for owner in ("Self assets","Spouse assets"):
                rows.append((2,label(owner)))
                if title:rows.append((2,label(title)))
                rows.append((2,ROW))
            source=self.source(rows)
            self.assertEqual(len(source["transactions"]),2)
            facts,_=self.candidate(source)
            self.assertEqual({r["owner"] for r in facts["transactions"]},{"Self","Spouse"})
            again,audit=self.candidate(source,facts)
            self.assertEqual(again,facts)
            self.assertEqual(audit["reports"][0]["quarantined_count"],0)
            self.assertEqual(adapt_oge_278t_extraction(source,record())["counts"]["qualified"],2)

    def test_scope_loss_requires_owner_restatement_not_just_new_account(self):
        source=self.source([(2,label("Spouse assets")),(2,label("Investment account #1")),(2,ROW),
            (4,label("Investment account #2")),(4,ROW)])
        self.assertEqual(len(source["transactions"]),1)
        self.assertEqual(source["transactions"][0]["owner"],"Spouse")
        self.assertIn("owner_context_unresolved",source["quarantined"][0]["reasons"])
        result,_=self.candidate(source);self.assertEqual(len(result["transactions"]),1)
        ledger=adapt_oge_278t_extraction(source,record())
        self.assertEqual(ledger["counts"]["qualified"],1)
        self.assertEqual(ledger["counts"]["quarantined"],1)
        recovered=self.source([(2,label("Spouse assets")),(2,ROW),(4,label("Spouse assets")),(4,ROW)])
        self.assertEqual(len(recovered["transactions"]),2)

    def test_same_account_duplicate_is_not_a_second_disclosure_but_new_page_is(self):
        source=self.source([(2,label("Investment account #1")),(2,ROW),(2,ROW)])
        self.assertEqual(source["transactions"],[])
        self.assertEqual(len(source["quarantined"]),2)
        self.assertEqual(self.candidate(source)[0]["transactions"],[])
        source=self.source([(2,label("Investment account #1")),(2,ROW),(3,ROW)])
        self.assertEqual(len(self.candidate(source)[0]["transactions"]),2)

    def test_replay_rejects_edited_scope_id_disposition_or_same_total_wrong_locator(self):
        from unison_snapshot.oge import OgeCatalogError
        source=self.source([(2,label("Investment account #1")),(2,ROW),(2,label("Investment account #2")),(2,ROW)])
        for field,value in [("account_title","Forged account"),("owner","Spouse"),("extraction_id","oge-278t:"+"f"*24),
                            ("source_rows_index",3)]:
            bad=deepcopy(source);bad["transactions"][0][field]=value
            for consume in (self.candidate,lambda x:adapt_oge_278t_extraction(x,record())):
                with self.subTest(field=field),self.assertRaises(OgeCatalogError):consume(bad)
        bad=deepcopy(source);bad.pop("row_interpretation_mode")
        with self.assertRaises(OgeCatalogError):self.candidate(bad)
        bad=deepcopy(source);bad["structural_rows"]=[]
        with self.assertRaises(OgeCatalogError):self.candidate(bad)
        # Transaction list order must also replay, not merely have equal counts.
        bad=deepcopy(source);bad["transactions"].reverse()
        with self.assertRaises(OgeCatalogError):self.candidate(bad)
        # The two physically equal raw rows have different locators/accounts.
        ledger=adapt_oge_278t_extraction(source,record())
        values=[r for r in ledger["rows"] if r["disposition"]=="qualified"]
        self.assertEqual(len(values),2)

    def test_old_formal_id_is_not_duplicated_when_new_account_binding_is_unproven(self):
        old=self.source([(2,ROW)],generic=False)
        prior,_=self.candidate(old)
        source=self.source([(2,label("Investment account #1")),(2,ROW),(2,label("Investment account #2")),(2,ROW)])
        result,audit=self.candidate(source,prior,{source["document_id"]:old})
        self.assertEqual(result["transactions"],prior["transactions"])
        self.assertEqual(audit["reports"][0]["legacy_inheritance"]["blocked_new_count"],2)
        unbound,_=self.candidate(source,prior)
        self.assertEqual(unbound["transactions"],prior["transactions"])

    def test_current_source_repeat_is_idempotent_without_legacy_cache_and_differences_stay_protected(self):
        source=self.source([(2,label("Investment account #1")),(2,ROW),(2,label("Investment account #2")),(2,ROW)])
        first,audit=self.candidate(source)
        second,repeated=self.candidate(source,first)
        self.assertEqual(second,first)
        self.assertEqual(repeated["reports"][0]["quarantined_count"],0)
        self.assertEqual(repeated["reports"][0]["promoted_count"],2)
        self.assertEqual(repeated["reports"][0]["legacy_inheritance"]["blocked_new_count"],0)
        self.assertEqual(set(repeated["reports"][0]["legacy_inheritance"]["inherited_ids"]),
                         {row["extraction_id"] for row in source["transactions"]})
        # Equality covers every published field, never just the fingerprint.
        for field,value in [("owner","Self"),("asset_name","Different"),("person_id","oge:other"),
                            ("filed_at","2026-06-01T00:00:00Z"),("amount_high",50000)]:
            changed=deepcopy(first);changed["transactions"][0][field]=value
            if field=="person_id":changed["people"].append({**changed["people"][0],"id":value})
            with self.subTest(field=field):
                protected,receipt=self.candidate(source,changed)
                self.assertEqual(protected["transactions"],changed["transactions"])
                self.assertGreater(receipt["reports"][0]["quarantined_count"],0)

    def test_public_reader_base_optional_nulls_repeat_without_loss_or_quarantine(self):
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        source=self.source([(2,ROW)])
        first,_=self.candidate(source)
        bundle=build(first,generated_at="2026-09-20T00:00:00Z",allow_production=True)
        public=PublicSnapshotRepository("example","data",transport=FakeTransport(bundle.files)).fetch().snapshot
        repeated,audit=self.candidate(source,public)
        self.assertEqual(audit["reports"][0]["quarantined_count"],0)
        self.assertEqual(repeated["transactions"],public["transactions"])
        # Model the existing optional-cell omission explicitly too.
        compact=deepcopy(first)
        for fact in compact["transactions"]:
            for key in list(fact):
                if fact[key] is None:fact.pop(key)
        repeated,audit=self.candidate(source,compact)
        self.assertEqual(repeated["transactions"],compact["transactions"])
        self.assertEqual(audit["reports"][0]["quarantined_count"],0)

    def test_normal_runner_without_original_preserves_legacy_owner_and_cache(self):
        from unison_snapshot.pipeline_qa.oge_run import build_oge_shadow_run
        source=self.source([(2,ROW)])
        old=self.source([(2,ROW)],generic=False)
        prior,_=self.candidate(old)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            inputs={"catalog":{"schema_version":SCHEMA,"transactions":[record()]},"archive":{
                "schema_version":"oge-278t-archive-batch/v1","reports":[{**record(),"document_id":source["document_id"],"sha256":source["source_sha256"],"byte_length":len(PDF),
                    "archive_path":f"oge/reports/{source['document_id']}/{source['source_sha256']}.pdf"}],
                "catalog_direct_count":1,"pending_count":0,"archived_count":1,"attempted_count":1,"failure_count":0,"failures":[]},"batch":{
                "schema_version":"oge-278t-extraction-batch/v1","report_count":1,"extraction_count":1,"failure_count":0,"failures":[]},
                "saved":{},"base":prior,"legacy":{source["document_id"]:old}}
            for name,value in inputs.items():(root/f"{name}.json").write_text(json.dumps(value),encoding="utf-8")
            (root/"extractions").mkdir();path=root/"extractions"/f"{source['document_id']}.json"
            path.write_text(json.dumps(source),encoding="utf-8");before=path.read_bytes()
            manifest=build_oge_shadow_run(catalog_path=root/"catalog.json",archive_batch_path=root/"archive.json",
                extraction_batch_path=root/"batch.json",extractions_dir=root/"extractions",output_dir=root/"out",
                saved_observations_path=root/"saved.json",candidate_base_path=root/"base.json",legacy_extractions_path=root/"legacy.json",
                data_cutoff_at="2026-09-20T00:00:00Z",run_id="scope-legacy",code_commit="a"*40,
                evidence_commit="b"*40,started_at="2026-09-20T00:00:00Z",completed_at="2026-09-20T00:01:00Z")
            self.assertEqual(path.read_bytes(),before)
            result=json.loads((root/"out/candidate.json").read_bytes())
            self.assertEqual([r["id"] for r in result["transactions"]],[r["id"] for r in prior["transactions"]])
            # A cached generic owner is not authority to rewrite an old formal fact.
            self.assertEqual(source["transactions"][0]["owner"],"Undisclosed")
            self.assertEqual(prior["transactions"][0]["owner"],"Self")
            self.assertEqual(result["transactions"][0]["owner"],"Self")
            self.assertEqual(manifest["counts"]["qualified_rows"],1)
            self.assertEqual(manifest["counts"]["quarantined_rows"],0)

    def test_new_reuse_requires_mode_and_complete_source_replay_old_inputs_stay_readable(self):
        from unison_snapshot.oge_reports import load_reusable_extraction,GENERIC_PARSER_VERSION
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);meta=archive_direct_pdf(root,record(),OgeSourceConfig(True,True),client=Client(),retrieved_at="2026-09-20T00:00:00Z")
            path=root/"oge/reports"/meta["document_id"]/f"{meta['sha256']}.json"
            source=self.source([(2,ROW)])
            target=root/"review"/meta["document_id"]/meta["sha256"]/(GENERIC_PARSER_VERSION.replace("/","-")+".json")
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(source),encoding="utf-8")
            self.assertEqual(load_reusable_extraction(root,path,root/"review",generic=True),source)
            for key in ("row_interpretation_mode","structural_rows"):
                old=deepcopy(source);old.pop(key)
                target.write_text(json.dumps(old),encoding="utf-8")
                self.assertIsNone(load_reusable_extraction(root,path,root/"review",generic=True))
            # A genuinely old v7 payload is readable; only reuse is invalidated.
            old=deepcopy(source);old.pop("row_interpretation_mode");old.pop("structural_rows")
            for row in old["transactions"]+old["quarantined"]:
                row.pop("source_rows_index",None);row.pop("account_context",None)
            self.assertEqual(len(self.candidate(old)[0]["transactions"]),1)
            edited=deepcopy(source);edited["transactions"][0]["owner"]="Spouse"
            target.write_text(json.dumps(edited),encoding="utf-8")
            self.assertIsNone(load_reusable_extraction(root,path,root/"review",generic=True))

if __name__=="__main__":unittest.main()
