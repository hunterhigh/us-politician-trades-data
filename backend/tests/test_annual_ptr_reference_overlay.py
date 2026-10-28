"""Annual tail integration; source application replay is tested by its producer."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from unison_snapshot.whitehouse_annual_candidate import overlay_whitehouse_annual_candidate

CUTOFF="2026-10-21T00:00:00Z"

def candidate():
    return {"meta":{"is_demo":False,"data_cutoff_at":CUTOFF,"transaction_disclosure_relations":[
        {"transaction_id":"annual","possible_ptr_transaction_ids":["old"],"status":"possible_ptr_overlap"}]},
        "people":[],"reported_holdings":[],"transactions":[{"id":"annual","person_id":"person"},
        {"id":"new","person_id":"person"}]}

def setup(review,pairs=(("old","new"),)):
    views={};reports=[]
    for index,(old,new) in enumerate(pairs):
        doc=str(index)
        item={"effective_prior_transaction_id":old,"transaction_id":new,"amendment_document_id":doc,
              "amendment_source_sha256":"a"*64,"application_status":"applied"}
        view={"document_id":doc,"observation_receipt":{"source":"source-replay-contract"},
              "native_exhibit_replacement_applications":[item],"native_exhibit_superseded_transaction_ids":[old],
              "transactions":[{"id":new}],"quarantined":[]}
        views[doc]=view
        reports.append({**{k:deepcopy(view[k]) for k in ("document_id","observation_receipt",
            "native_exhibit_replacement_applications","native_exhibit_superseded_transaction_ids")},
            "native_exhibit_application_context":{"schema_version":"test-context","data_cutoff_at":CUTOFF},
            "promoted_count":1,"quarantined_count":0})
    path=review/"oge/qualifications/current.json";path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"data_cutoff_at":CUTOFF,"reports":reports}),encoding="utf-8")
    return views,path

class AnnualReferenceOverlayTests(unittest.TestCase):
    def test_no_context_preserves_output_and_existing_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            before=candidate();after,audit=overlay_whitehouse_annual_candidate(before,Path(folder))
            self.assertEqual(after,before)
            self.assertNotIn("native_ptr_reference_reconciliation",audit)

    def test_tail_maps_only_references_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);views,_=setup(root);before=candidate()
            with patch("unison_snapshot.oge_native_replacements.verify_native_exhibit_context",return_value=views) as replay:
                after,audit=overlay_whitehouse_annual_candidate(before,root,evidence_root=root/"evidence")
                again,again_audit=overlay_whitehouse_annual_candidate(after,root,evidence_root=root/"evidence")
            self.assertEqual(after["transactions"],before["transactions"])
            self.assertEqual(after["reported_holdings"],before["reported_holdings"])
            self.assertEqual(after["meta"]["transaction_disclosure_relations"][0]["possible_ptr_transaction_ids"],["new"])
            self.assertEqual(len(audit["native_ptr_reference_reconciliation"]["replacements"]),1)
            self.assertEqual(after,again);self.assertEqual(audit,again_audit)
            self.assertEqual(replay.call_args.args[1],root/"evidence")

    def test_context_requires_controlled_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);setup(root)
            with self.assertRaisesRegex(ValueError,"controlled evidence root"):
                overlay_whitehouse_annual_candidate(candidate(),root)

    def test_source_count_and_target_failures_are_not_silenced(self):
        for failure in ("count","source","missing_target","wrong_person"):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);views,path=setup(root);data=candidate()
                if failure=="count":
                    raw=json.loads(path.read_text());raw["reports"][0]["promoted_count"]=2;path.write_text(json.dumps(raw))
                if failure=="missing_target":data["transactions"].pop()
                if failure=="wrong_person":data["transactions"][-1]["person_id"]="other"
                kwargs={"side_effect":ValueError("source bytes changed")} if failure=="source" else {"return_value":views}
                with patch("unison_snapshot.oge_native_replacements.verify_native_exhibit_context",**kwargs):
                    with self.assertRaises(ValueError):
                        overlay_whitehouse_annual_candidate(data,root,evidence_root=root/"evidence")

    def test_cycle_and_conflicting_successors_rejected(self):
        for pairs,error in [((("old","new"),("new","old")),"cycle"),
                            ((("old","new"),("old","different")),"conflicting successors")]:
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder);views,_=setup(root,pairs)
                with patch("unison_snapshot.oge_native_replacements.verify_native_exhibit_context",return_value=views):
                    with self.assertRaisesRegex(ValueError,error):
                        overlay_whitehouse_annual_candidate(candidate(),root,evidence_root=root/"evidence")

    def test_script_passes_controlled_root_to_normal_overlay(self):
        from unison_snapshot.commands import overlay_whitehouse_annual_candidate as script
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/"candidate.json";path.write_text(json.dumps(candidate()))
            with patch.object(script,"overlay_whitehouse_annual_candidate",return_value=(candidate(),{})) as call,                 patch.object(sys,"argv",["overlay","--review-root",str(root/"review"),"--evidence-root",str(root/"evidence"),
                    "--candidate",str(path),"--audit-out",str(root/"audit.json")]),redirect_stdout(io.StringIO()):
                script.main()
            self.assertEqual(call.call_args.kwargs["evidence_root"],root/"evidence")

    def test_normal_script_consumes_reference_mapping_and_fails_before_writes(self):
        from unison_snapshot.commands import overlay_whitehouse_annual_candidate as script
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);review=root/"review";views,_=setup(review)
            path=root/"candidate.json";path.write_text(json.dumps(candidate()))
            args=["overlay","--review-root",str(review),"--evidence-root",str(root/"evidence"),
                  "--candidate",str(path),"--audit-out",str(root/"audit.json")]
            with patch("unison_snapshot.oge_native_replacements.verify_native_exhibit_context",return_value=views), \
                 patch.object(sys,"argv",args),redirect_stdout(io.StringIO()):
                script.main()
            result=json.loads(path.read_text())
            self.assertEqual(result["meta"]["transaction_disclosure_relations"][0]["possible_ptr_transaction_ids"],["new"])
            self.assertEqual(result["transactions"],candidate()["transactions"])
            self.assertEqual(len(json.loads((root/"audit.json").read_text())["native_ptr_reference_reconciliation"]["replacements"]),1)
            preserved=path.read_bytes();audit=(root/"audit.json").read_bytes()
            with patch("unison_snapshot.oge_native_replacements.verify_native_exhibit_context",side_effect=ValueError("source changed")), \
                 patch.object(sys,"argv",args),self.assertRaisesRegex(ValueError,"source changed"):
                script.main()
            self.assertEqual(path.read_bytes(),preserved)
            self.assertEqual((root/"audit.json").read_bytes(),audit)

    def test_both_production_workflows_pass_known_controlled_root(self):
        root=Path(__file__).resolve().parents[2]
        for name in ("oge.yml","whitehouse.yml"):
            text=(root/".github/workflows"/name).read_text(encoding="utf-8")
            command=text.split("python -m unison_snapshot overlay-whitehouse-annual-candidate",1)[1].split("--audit-out",1)[0]
            self.assertIn('--evidence-root "$EVIDENCE_ROOT"',command)
        text=(root/".github/workflows/whitehouse.yml").read_text(encoding="utf-8")
        self.assertIn('uses: ./.github/actions/data-trees',text)
        self.assertIn('scope: whitehouse',text)
