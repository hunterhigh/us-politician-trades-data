"""Opt-in generic WH contracts; synthetic pages are not original accuracy gold."""
from copy import deepcopy
import json
import io
from contextlib import redirect_stdout
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_whitehouse_278t_candidate import _base, _catalog
from unison_snapshot.whitehouse_278t import (parse_generic_whitehouse_278t_pdf,
    _generic_table_rows, validate_generic_whitehouse_report, GENERIC_PARSER_VERSION)
from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t
from unison_snapshot.whitehouse_278t_candidate import build_whitehouse_278t_review_candidate, _transaction
from unison_snapshot.builder import build
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.oge import OgeCatalogError

PDF=b"%PDF-1.7\nsynthetic native WH contract\n%%EOF"
SHA=hashlib.sha256(PDF).hexdigest()
URL="https://www.whitehouse.gov/wp-content/uploads/2026/06/Wiles-PTR.pdf"
DOC="wh-url:"+"a"*24
COVER=("Periodic Transaction Report (OGE Form 278-T)\nFiler's Information\nWiles, Susie\n"
       "Assistant to the President and Chief of Staff - White House\n"
       "Filer's Certification\nDate: 06/13/2026\nAgency Ethics Official's Opinion\nDate: 06/15/2026\n")
ROW=["1","ALPHA","Exchange","06/01/2026","No","Over $50,000,000"]

def report(rows=None,cover=COVER,legacy=None):
    rows=[(2,ROW)] if rows is None else rows
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/"source.pdf";path.write_bytes(PDF)
        with patch("unison_snapshot.whitehouse_278t._extract_pdf", return_value=(cover,rows,rows,
                {"page_number":1,"text":cover,"document_page_count":3})), patch(
                "unison_snapshot.whitehouse_278t._extract_ocr_pdf", side_effect=AssertionError("new OCR forbidden")):
            return parse_generic_whitehouse_278t_pdf(path,source_url=URL,source_sha256=SHA,
                document_id=DOC,filer_name="Wiles, Susie",legacy_extraction=legacy)

def consume(item,base=None):
    base=_base() if base is None else base
    audit=audit_whitehouse_278t([item],_catalog(),base)
    return build_whitehouse_278t_review_candidate(base,[item],audit,_catalog(),data_cutoff_at="2026-06-20T00:00:00Z")

class GenericWhiteHouseTests(unittest.TestCase):
    def test_native_roles_open_exchange_undisclosed_reach_existing_builder(self):
        item=report()
        candidate,audit=consume(item)
        self.assertEqual(audit["promoted_transaction_count"],1)
        row=candidate["transactions"][0]
        self.assertEqual((row["owner"],row["transaction_type"]),("Undisclosed","exchange"))
        self.assertEqual((row["amount_low"],row["amount_high"],row["amount_low_exclusive"]),(50000000,None,True))
        self.assertTrue(build(candidate,generated_at="2026-06-20T00:00:00Z",allow_production=True).manifest["snapshot_id"])

    def test_receipt_or_agency_date_cannot_replace_filer_date(self):
        item=report(cover=COVER.replace("06/13/2026","unknown")+"OGE Received 06/16/2026")
        self.assertIsNone(item["filed_at"])
        candidate,audit=consume(item)
        self.assertEqual(candidate["transactions"],[])
        self.assertIn("filer_signature_date_missing",audit["reports"][0]["document_reasons"])

    def test_explicit_account_records_remain_distinct_and_display_titles(self):
        rows=[(2,["","Investment account #1","","","",""]),(2,ROW),
              (2,["","Investment account #2","","","",""]),(2,ROW)]
        candidate,audit=consume(report(rows))
        self.assertEqual(audit["promoted_transaction_count"],2)
        self.assertEqual(len({row["id"] for row in candidate["transactions"]}),2)
        self.assertEqual({row["asset_name"] for row in candidate["transactions"]},
            {"ALPHA — Investment account #1","ALPHA — Investment account #2"})

    def test_same_page_unknown_does_not_clear_explicit_owner_scope(self):
        rows=[(2,["","Spouse assets","","","",""]),(2,["#","","","","",""]),(2,ROW)]
        good,bad,_=_generic_table_rows(rows,source_sha256=SHA)
        self.assertEqual(len(good),1)
        self.assertEqual(good[0]["owner"],"Spouse")
        self.assertEqual(bad,[])
        rows.insert(2,(2,["","Spouse retirement assets unclear","","","",""]))
        good,bad,_=_generic_table_rows(rows,source_sha256=SHA)
        self.assertEqual(good,[])
        self.assertIn("account_context_unresolved",bad[-1]["reasons"])

    def test_duplicate_physical_number_ambiguous_but_other_rows_survive(self):
        row=ROW[:];row[-1]="$1,001 - $15,000"
        other=row[:];other[0]="2";other[1]="BETA"
        item=report([(2,row),(2,row),(2,other)])
        validate_generic_whitehouse_report(item)
        self.assertEqual(len(item["transactions"]),1)
        self.assertEqual(len(item["quarantined"]),2)
        candidate,_=consume(item)
        self.assertEqual(len(candidate["transactions"]),1)
        self.assertNotIn("amount_low_exclusive",candidate["transactions"][0])

    def test_source_row_or_role_evidence_cannot_be_edited(self):
        item=report();item["transactions"][0]["amount_high"]=50000000
        with self.assertRaisesRegex(OgeCatalogError,"source interpretation"):
            consume(item)
        item=report();item["cover_date_evidence"]["filed_at"]="2026-06-15"
        with self.assertRaisesRegex(OgeCatalogError,"cover role"):
            consume(item)

    def test_legacy_identity_and_approved_default_correction_are_separate(self):
        row=ROW[:];row[2]="Purchase";row[-1]="$1,001 - $15,000"
        oldrows,_=parse_table_rows([(2,row)],source_sha=SHA)
        legacy={"schema_version":"whitehouse-278t-extraction/v1","parser_version":"whitehouse-278t-hybrid-geometry/v2",
            "document_id":DOC,"source_url":URL,"source_sha256":SHA,"filer_name":"Wiles, Susie",
            "transactions":oldrows,"quarantined":[]}
        # Real early WH caches omit cells; replay of the original full-input
        # stable hash is required rather than matching a printed row number.
        legacy["transactions"][0].pop("cells")
        new=report([(2,row)],legacy=legacy)
        self.assertEqual(new["transactions"][0]["extraction_id"],oldrows[0]["extraction_id"])
        original=deepcopy(new);original["parser_version"]="whitehouse-278t-hybrid-geometry/v2"
        base=_base()
        from unison_snapshot.oge_candidate import _person_id,_identity_key
        person_id=_person_id(_identity_key(_catalog()["reports"][0]))
        from unison_snapshot.whitehouse_278t_candidate import _person
        base["people"]=[_person(_catalog()["reports"][0])]
        oldfact=_transaction(original,oldrows[0],person_id)
        base["transactions"]=[oldfact]
        base["meta"]["data_cutoff_at"]="2026-06-13T00:00:00Z"
        candidate,audit=consume(new,base)
        self.assertEqual(len(candidate["transactions"]),1)
        self.assertEqual(candidate["transactions"][0]["id"],oldfact["id"])
        self.assertEqual(candidate["transactions"][0]["owner"],"Undisclosed")
        self.assertEqual(base["transactions"][0]["owner"],"Self")
        self.assertEqual(audit["existing_transaction_mutation_count"],1)
        self.assertEqual(audit["promoted_transaction_count"],0)
        self.assertTrue(audit["source_row_conservation_complete"])
        base["transactions"][0]["amount_low"]=1002
        candidate,audit=consume(new,base)
        self.assertEqual(candidate["transactions"],base["transactions"])
        self.assertIn("legacy_report_new_row_binding_pending",audit["reports"][0]["rows"][0]["reasons"])

    def test_serialized_legacy_owner_corrections_are_reproved_not_rule_labels(self):
        raw=ROW[:];raw[2]="Purchase";raw[-1]="$1,001 - $15,000"
        oldrows,_=parse_table_rows([(2,raw)],source_sha=SHA)
        legacy={"schema_version":"whitehouse-278t-extraction/v1","parser_version":"whitehouse-278t-hybrid-geometry/v2",
            "document_id":DOC,"source_url":URL,"source_sha256":SHA,"filer_name":"Wiles, Susie","transactions":oldrows,"quarantined":[]}
        original=report([(2,raw)],legacy=legacy)
        self.assertTrue(original["transactions"][0]["legacy_inheritance"]["owner_correction"])
        mutations=[lambda row:row["legacy_inheritance"]["legacy_row"].update(extraction_id="oge-278t:"+"f"*24),
            lambda row:row["legacy_inheritance"]["legacy_row"].update(amount_low=999),
            lambda row:row["legacy_inheritance"]["owner_correction"].update(new_value="Self"),
            lambda row:row.update(source_rows_index=999)]
        for mutate in mutations:
            changed=deepcopy(original);mutate(changed["transactions"][0])
            with self.assertRaises(OgeCatalogError):
                consume(changed)
        explicit=report([(2,["","Self assets","","","",""]),(2,raw)],legacy=legacy)
        self.assertNotIn("owner_correction",explicit["transactions"][0]["legacy_inheritance"])
        self.assertEqual(explicit["transactions"][0]["owner"],"Self")
        conflicting=report([(2,["","Spouse assets","","","",""]),(2,raw)],legacy=legacy)
        candidate,audit=consume(conflicting)
        self.assertEqual(candidate["transactions"],[])
        self.assertIn("legacy_field_reconciliation_pending",audit["reports"][0]["rows"][0]["reasons"])

    def test_generic_and_exact_source_alias_consumer_combine_without_double_counting(self):
        from unison_snapshot.whitehouse_278t_candidate import (
            audit_whitehouse_278t_exact_source_candidate,build_whitehouse_278t_exact_source_candidate)
        first=report();alias=deepcopy(first)
        alias.update(document_id="wh-url:"+"b"*24,source_url=URL.replace("Wiles-PTR","Wiles-PTR-copy"))
        audit=audit_whitehouse_278t_exact_source_candidate(_base(),[first,alias],_catalog())
        candidate,result=build_whitehouse_278t_exact_source_candidate(_base(),[first,alias],audit,_catalog(),
            data_cutoff_at="2026-06-20T00:00:00Z")
        self.assertEqual(len(candidate["transactions"]),1)
        self.assertEqual(result["exact_source_alias_row_count"],1)
        self.assertTrue(result["source_row_conservation_complete"])
        # Reconsume with the same source-bound formal identity and approved
        # source observations: both alias receipts must still point at it.
        oldrow=ROW[:];oldrow[2]="Purchase";oldrow[-1]="$1,001 - $15,000"
        raw,_=parse_table_rows([(2,oldrow)],source_sha=SHA)
        legacy={"schema_version":"whitehouse-278t-extraction/v1","parser_version":"whitehouse-278t-hybrid-geometry/v2",
            "document_id":DOC,"source_url":URL,"source_sha256":SHA,"filer_name":"Wiles, Susie","transactions":raw,"quarantined":[]}
        first=report([(2,oldrow)],legacy=legacy);alias=deepcopy(first)
        alias.update(document_id="wh-url:"+"b"*24,source_url=URL.replace("Wiles-PTR","Wiles-PTR-copy"))
        from unison_snapshot.whitehouse_278t_candidate import _person
        identity=_catalog()["reports"][0];person=_person(identity)
        base=_base();base["meta"]["data_cutoff_at"]="2026-06-20T00:00:00Z";base["people"]=[person]
        older=deepcopy(first);older["parser_version"]="whitehouse-278t-hybrid-geometry/v2"
        base["transactions"]=[_transaction(older,raw[0],person["id"])]
        audit=audit_whitehouse_278t_exact_source_candidate(base,[first,alias],_catalog())
        candidate,result=build_whitehouse_278t_exact_source_candidate(base,[first,alias],audit,_catalog(),data_cutoff_at="2026-06-20T00:00:00Z")
        self.assertEqual(len(candidate["transactions"]),1)
        self.assertEqual(candidate["transactions"][0]["owner"],"Undisclosed")
        self.assertEqual(result["retained_existing_transaction_count"],1)
        self.assertEqual(result["reports"][0]["retained_ids"],[raw[0]["extraction_id"]])
        self.assertEqual(result["reports"][0]["parser_version"],GENERIC_PARSER_VERSION)
        self.assertTrue(all(row["formal_transaction_id"]==raw[0]["extraction_id"]
            for source in result["exact_source_aliases"][0]["aliases"] for row in source["rows"]))

    def test_generic_cli_uses_separate_inputs_and_default_selection_stays_legacy(self):
        from test_build_whitehouse_278t_candidate import WhiteHouse278TCandidateCliTests, _write
        from scripts.build_whitehouse_278t_candidate import main
        from scripts.whitehouse_coverage import _trade_versions
        case=WhiteHouse278TCandidateCliTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.report=report()
        case.report["document_id"]="wh-url:"+hashlib.sha256(URL.encode()).hexdigest()[:24]
        case.coverage["reports"][0].update(document_id=case.report["document_id"],document_url=URL,
            archive_sha256_versions=[SHA],extraction_parser_version=GENERIC_PARSER_VERSION)
        case.extraction=case.review/"whitehouse/extractions"/case.report["document_id"][7:]/SHA/(GENERIC_PARSER_VERSION.replace("/","-")+".json")
        # The preexisting legacy fixture is a different source, so remove that
        # fixture before this independent fixed one-source integration.
        for old in (case.review/"whitehouse/extractions").glob("*/*/*.json"):
            old.unlink()
        case._write_inputs()
        base=_base();base["meta"]["data_cutoff_at"]="2026-06-20T00:00:00Z"
        _write(case.oge,base)
        coverage_path=case.review/"coverage-v5.json";status_path=case.review/"status-v5.json"
        _write(coverage_path,case.coverage);_write(status_path,case.status)
        with redirect_stdout(io.StringIO()):
            code=main(["--oge-candidate",str(case.oge),"--review-root",str(case.review),"--candidate-out",str(case.oge),
                "--audit-out",str(case.audit_out),"--coverage-input",str(coverage_path),"--extraction-status-input",str(status_path)])
        self.assertEqual(code,0)
        result=json.loads(case.audit_out.read_text(encoding="utf-8"))
        self.assertEqual(result["promoted_transaction_count"],1)
        final=json.loads(case.oge.read_text(encoding="utf-8"))
        self.assertIsNone(final["transactions"][0]["amount_high"])
        self.assertNotIn(GENERIC_PARSER_VERSION,_trade_versions(URL,SHA))
        self.assertEqual(_trade_versions(URL,SHA,generic_trade=True),(GENERIC_PARSER_VERSION,))

    def test_extraction_cli_is_explicit_fixed_source_without_default_cache_changes(self):
        from test_whitehouse_extract import WhiteHouseExtractTests, script
        case=WhiteHouseExtractTests();case.setUp();self.addCleanup(case.doCleanups)
        metadata=case.archive("a")
        def parse(_,**kwargs):
            return {"source_url":kwargs["source_url"],"source_sha256":kwargs["source_sha256"],
                "parser_version":GENERIC_PARSER_VERSION,"document_reasons":[],"transactions":[]}
        with self.assertRaisesRegex(ValueError,"fixed document"):
            script.extract_batch(case.evidence,case.review,limit=1,generic_trade=True)
        with patch.object(script,"parse_generic_whitehouse_278t_pdf",side_effect=parse) as parser, patch.object(
                script,"parse_whitehouse_278t_pdf",side_effect=AssertionError("legacy path unexpected")):
            actual=script.extract_batch(case.evidence,case.review,limit=1,generic_trade=True,
                document_id=metadata["document_id"],source_sha256=metadata["sha256"])
        self.assertEqual(actual["extraction_created_count"],1)
        self.assertEqual(parser.call_count,1)
        folder=case.review/"whitehouse/extractions"/metadata["document_id"][7:]/metadata["sha256"]
        self.assertTrue((folder/(GENERIC_PARSER_VERSION.replace("/","-")+".json")).is_file())
        self.assertFalse((folder/(script.TRADE_PARSER_VERSION.replace("/","-")+".json")).exists())

    def test_exhibit_and_unreadable_scan_remain_unknown_without_new_ocr(self):
        item=report(cover=COVER+"EXHIBIT A\nreplacement transactions")
        candidate,audit=consume(item)
        self.assertEqual(candidate["transactions"],[])
        self.assertIn("replacement_block_relationship_unresolved",audit["reports"][0]["document_reasons"])
        item=report([],cover="")
        self.assertEqual(item["transactions"],[])
        self.assertIn("transaction_table_not_found_native_text_only",item["document_reasons"])

if __name__=="__main__":unittest.main()
