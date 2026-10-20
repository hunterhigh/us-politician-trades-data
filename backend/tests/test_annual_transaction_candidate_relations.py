"""Synthetic confirmed-copy and possible-overlap cases in real candidates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_house_candidate import BASE, TRANSACTION, IDENTITY
from tests.test_house_annual_sections import word
from tests.test_senate_candidate import BASE as SENATE_BASE, identity, extraction
from tests.test_senate_annual_transactions import sample
from unison_snapshot.house_annual_sections import observe_annual_sections, consume_schedule_b_inputs
from unison_snapshot.house_candidate import build_house_candidate, load_house_candidate
from unison_snapshot.house import HouseIndexError
from unison_snapshot.senate_annual_transactions import observe_part4b
from unison_snapshot.senate_candidate import build_senate_candidate
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.builder import build


def house_input(document="10000001", raw=b"synthetic annual PDF bytes"):
    sha=hashlib.sha256(raw).hexdigest()
    source={"document_id":document,"source_id":"house_clerk","filing_year":2025,
            "filing_type":"O","filed_date":"2026-05-01","report_type":"Annual Report",
            "source_url":f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025/{document}.pdf"}
    extract={"schema_version":"house-holding-extraction/v1","source_sha256":sha,"source":source,"rows":[]}
    page={"width":612,"height":792,"words":[word(t,x,y) for t,x,y in [
        ("Asset",25,100),("Owner",280,100),("Date",319,100),("Tx.",401,100),
        ("Amount",440,100),("Cap.",523,100),("Example",25,130),("[ST]",100,130),
        ("SP",280,130),("02/18/2025",319,130),("S",401,130),
        ("$1,001",440,130),("-",475,130),("$15,000",481,130)]]}
    person=dict(IDENTITY,document_id=document)
    return {"observation":observe_annual_sections([page],extract),"extraction":extract,
            "identity":person,"pages":[page],"source_bytes":raw}


def house_fixture(root):
    from tests.test_house_candidate import HouseCandidateTests
    HouseCandidateTests().fixture(root)
    path=root/"house_clerk/qualifications/2026/20000001/a.json"
    q=json.loads(path.read_text())
    q["transactions"][0].update(owner="Spouse",ticker=None,ticker_mapping_basis=None,
        transaction_type="sale",transaction_date="2025-02-18")
    path.write_text(json.dumps(q))
    return q["transactions"][0],{"status":"ok","run_at":"2026-09-18T12:00:00Z",
                                 "counts":{"archived":1,"pending":0,"failed":0}}


class HouseAnnualCandidateRelationTests(unittest.TestCase):
    def test_real_candidate_consumes_possible_overlap_without_deleting_formal_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old,state=house_fixture(root);audit={}
            data=build_house_candidate(root,state,deepcopy(BASE),
                annual_transaction_inputs=[house_input()],annual_transaction_audit=audit)
            self.assertEqual(data["transactions"],[old])
            row=audit["reports"][0]["rows"][0]
            self.assertEqual(row["relation_status"],"possible_ptr_overlap")
            self.assertEqual(row["possible_ptr_transaction_ids"],[old["id"]])
            self.assertIn("field_match_without_official_row_link",row["candidate_blocking_reasons"])
            self.assertTrue(build(data,generated_at="2026-09-18T12:00:00Z",allow_production=True).manifest["snapshot_id"])

    def test_equal_formal_rows_keep_both_ids_and_only_possible_correspondence(self):
        item=house_input();annual=item["observation"]["schedule_b_rows"][0]
        tx={k:annual.get(k) for k in ["asset_name","owner","ticker","transaction_type",
                                    "transaction_date","amount_low","amount_high"]}
        tx.update(source_id="house_clerk",person_id=IDENTITY["person_id"],id="house-ptr:first")
        other=dict(tx,id="house-ptr:second")
        old=deepcopy([tx,other])
        audit=consume_schedule_b_inputs([item],transactions=old,reported_holdings=[])
        result=audit["reports"][0]["rows"][0]
        self.assertEqual(result["possible_ptr_transaction_ids"],[tx["id"],other["id"]])
        self.assertIn("multiple_field_matches_no_official_row_link",result["candidate_blocking_reasons"])
        self.assertEqual(old,[tx,other])

    def test_actual_candidate_excludes_only_verified_exact_report_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old,state=house_fixture(root);audit={}
            data=build_house_candidate(root,state,deepcopy(BASE),
                annual_transaction_inputs=[house_input(),house_input("10000002")],annual_transaction_audit=audit)
            self.assertEqual(data["transactions"],[old])
            self.assertEqual(audit["candidate_disposition_counts"],
                             {"blocked_pending_relationship":1,"excluded_exact_source_copy":1})
            alias=audit["reports"][1]["rows"][0]
            self.assertEqual(alias["canonical_document_id"],"10000001")
            self.assertIsNone(alias["formal_transaction_id"])
            self.assertTrue(build(data,generated_at="2026-09-18T12:00:00Z",allow_production=True).manifest["snapshot_id"])

    def test_field_equality_different_bytes_or_missing_bytes_does_not_resolve_copy(self):
        for mode in ["different","missing","mismatched","mutated"]:
            a,b=house_input(),house_input("10000002")
            if mode=="different":b=house_input("10000002",b"different original same financial fields")
            if mode=="missing":b.pop("source_bytes")
            if mode=="mismatched":b["source_bytes"]=b"wrong source bytes"
            if mode=="mutated":b["observation"]["schedule_b_rows"][0]["asset_name"]="Changed"
            with self.subTest(mode=mode):
                out=consume_schedule_b_inputs([a,b],transactions=[],reported_holdings=[])
                self.assertNotIn("excluded_exact_source_copy",out["candidate_disposition_counts"])
                self.assertTrue(all(r["candidate_blocking_reasons"] for p in out["reports"] for r in p["rows"]))

    def test_existing_loader_passes_optional_audit_and_preserves_default_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);_,state=house_fixture(root)
            state_path=root/"state.json";state_path.write_text(json.dumps(state))
            base_path=root/"base.json";base_path.write_text(json.dumps(BASE))
            before=load_house_candidate(root,state_path,base_path)
            audit={}
            after=load_house_candidate(root,state_path,base_path,annual_transaction_audit=audit)
            self.assertEqual(before,after)
            self.assertEqual(audit["observed_row_count"],0)
            self.assertEqual(audit["new_candidate_transaction_count"],0)

    def test_default_existing_entry_reads_saved_sidecar_and_preserves_old_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old,state=house_fixture(root);item=house_input();audit={}
            relative=Path("2025/10000001")/(item["observation"]["source_sha256"]+".json")
            for directory,value in [("holding_section_observations",item["observation"]),
                                    ("holding_extractions",item["extraction"]),
                                    ("holding_identities",item["identity"])]:
                path=root/"house_clerk"/directory/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(value))
            data=build_house_candidate(root,state,deepcopy(BASE),annual_transaction_audit=audit)
            self.assertEqual(data["transactions"],[old])
            self.assertEqual(audit["observed_row_count"],1)
            self.assertNotIn("annual_saved_page_reproduction_unavailable",audit["reports"][0]["rows"][0]["candidate_blocking_reasons"])
            self.assertIsInstance(audit["reports"][0]["source_text_coverage"],dict)
            observation_path=root/"house_clerk/holding_section_observations"/relative
            legacy=deepcopy(item['observation'])
            for name in ('source_word_pages','source_word_pages_sha256','source_text_coverage'):
                legacy.pop(name,None)
            legacy['schema_version']='house-annual-sections-observation/v3'
            observation_path.write_text(json.dumps(legacy))
            build_house_candidate(root,state,deepcopy(BASE),annual_transaction_audit=audit)
            self.assertIn('annual_saved_page_reproduction_unavailable',audit['reports'][0]['report_blocking_reasons'])
            corrupted=deepcopy(item['observation'])
            corrupted['source_word_pages'][0]['words'][0]['text']='changed'
            observation_path.write_text(json.dumps(corrupted))
            with self.assertRaisesRegex(HouseIndexError,'saved word pages digest differs'):
                build_house_candidate(root,state,deepcopy(BASE))


class SenateAnnualCandidateRelationTests(unittest.TestCase):
    def test_actual_candidate_consumes_verified_exact_annual_copy_at_row_level(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);review=root/"review";evidence=root/"evidence"
            ptr="11111111-1111-4111-8111-111111111111"
            from tests.test_senate_candidate import SenateCandidateTests
            state=SenateCandidateTests().fixture(review,[identity(ptr)],[extraction(ptr)])
            _,raw=sample();sha=hashlib.sha256(raw).hexdigest()
            canonical="22222222-2222-4222-8222-222222222222";alias="33333333-3333-4333-8333-333333333333"
            reports=[]
            for doc in [canonical,alias]:
                meta={"schema_version":"senate-efd-annual-archive/v1","document_id":doc,
                    "document_url":f"https://efdsearch.senate.gov/search/view/annual/{doc}/",
                    "source_sha256":sha,"byte_length":len(raw),"filer_name":"Sample Senator",
                    "office":"Sample, Senator (Senator)","report_year":2025,"amendment_number":0,
                    "portal_listed_date":"2026-05-01"}
                path=evidence/f"senate_efd/annual/reports/{doc}";path.mkdir(parents=True)
                (path/(sha+".html")).write_bytes(raw);(path/(sha+".metadata.json")).write_text(json.dumps(meta))
                observation=observe_part4b(meta,raw)
                relative=Path(f"senate_efd/annual/part4b/{doc}/{sha}/observation-v2.json")
                target=review/relative;target.parent.mkdir(parents=True);target.write_text(json.dumps(observation))
                reports.append({"document_id":doc,"person_id":"senate:S000001","source_sha256":sha,
                    "report_year":2025,"amendment_number":0,"part4b_printed_row_count":1,
                    "publication_state":"qualified" if doc==canonical else "exact_source_copy",
                    "part4b_observation_path":relative.as_posix(),
                    **({"exact_source_copy_of":canonical} if doc==alias else {})})
            copies=[{"person_id":"senate:S000001","report_year":2025,"amendment_number":0,
                     "source_sha256":sha,"canonical_document_id":canonical,"alias_document_ids":[alias],
                     "basis":"same_verified_archived_bytes_and_report_context"}]
            current=review/"senate_efd/annual/current.json"
            current.write_text(json.dumps({"schema_version":"senate-efd-annual-review/v1","source_id":"senate_efd",
                "roster_sha256":"b"*64,"people":[],"reported_holdings":[],"holding_count":0,
                "qualified_report_count":1,"reports":reports,"exact_source_copies":copies}))
            data,audit=build_senate_candidate(review,state,deepcopy(SENATE_BASE),evidence_root=evidence)
            decisions=audit["annual_transaction_candidate_decisions"]
            self.assertEqual(decisions["candidate_disposition_counts"],
                             {"blocked_pending_relationship":1,"excluded_exact_source_copy":1})
            self.assertEqual(len(data["transactions"]),1)
            self.assertEqual(data["transactions"][0]["id"],extraction(ptr)["transactions"][0]["extraction_id"])
            self.assertTrue(build(data,generated_at="2026-09-20T12:00:00Z",allow_production=True).manifest["snapshot_id"])
            (evidence/f"senate_efd/annual/reports/{alias}/{sha}.html").write_bytes(b"changed")
            with self.assertRaisesRegex(SenateEfdError,"differs from archived source"):
                build_senate_candidate(review,state,deepcopy(SENATE_BASE),evidence_root=evidence)
