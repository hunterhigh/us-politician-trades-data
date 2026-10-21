"""Cross-source reference graph contracts; verifier is mocked, not source accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock,patch
from test_annual_ptr_reference_overlay import candidate,setup
from unison_snapshot.oge_native_replacements import reconcile_annual_ptr_references


def application(old="old",new="new",doc="cross"):
    return {"prior_transaction_id":old,"effective_prior_transaction_id":old,"transaction_id":new,
            "amendment_document_id":doc,"amendment_source_sha256":"b"*64,"application_status":"applied",
            "prior_fact":{"id":old,"person_id":"person"}}

def wh_proof(root,applications):
    proof={"schema_version":"cross-source-contract","applications":applications}
    path=root/"whitehouse/qualifications/candidate-current.json";path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({"native_cross_source_applications":proof}),encoding="utf-8")
    return proof


def verifier(verified):
    module=ModuleType("unison_snapshot.oge_cross_source_replacements")
    module.verify_cross_source_native_applications=Mock(return_value=({},verified))
    return module

class CrossSourceAnnualReferenceTests(unittest.TestCase):
    def test_whitehouse_only_uses_verified_not_self_reported_mapping_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);saved=wh_proof(root,[application(new="unverified")])
            verified={"applications":[application(),application()]};module=verifier(verified);before=candidate()
            with patch.dict(sys.modules,{module.__name__:module}):
                after,audit=reconcile_annual_ptr_references(before,root,root/"evidence")
            self.assertEqual(after["transactions"],before["transactions"])
            self.assertEqual(after["meta"]["transaction_disclosure_relations"][0]["possible_ptr_transaction_ids"],["new"])
            self.assertEqual(len(audit["replacements"]),1)
            module.verify_cross_source_native_applications.assert_called_once_with(saved,root/"evidence",root)
            self.assertEqual(before,candidate())

    def test_no_context_preserves_same_object_without_optional_verifier(self):
        with tempfile.TemporaryDirectory() as folder:
            before=candidate();after,audit=reconcile_annual_ptr_references(before,Path(folder))
            self.assertIs(after,before);self.assertEqual(audit,{"replacements":[]})

    def test_missing_controlled_root_and_failed_replay_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);proof=wh_proof(root,[application()]);module=verifier(proof)
            with patch.dict(sys.modules,{module.__name__:module}):
                with self.assertRaisesRegex(ValueError,"controlled evidence root"):
                    reconcile_annual_ptr_references(candidate(),root)
                module.verify_cross_source_native_applications.assert_not_called()
                module.verify_cross_source_native_applications.side_effect=ValueError("source replay differs")
                before=candidate()
                with self.assertRaisesRegex(ValueError,"source replay differs"):
                    reconcile_annual_ptr_references(before,root,root/"evidence")
                self.assertEqual(before,candidate())

    def test_combined_oge_and_wh_chain_resolves_final_same_person(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);views,_=setup(root,(("old","middle"),))
            proof=wh_proof(root,[application("middle","new")]);module=verifier(proof)
            with patch.dict(sys.modules,{module.__name__:module}),patch(
                    "unison_snapshot.oge_native_replacements.verify_native_exhibit_context",return_value=views):
                first,audit=reconcile_annual_ptr_references(candidate(),root,root/"evidence")
                again,again_audit=reconcile_annual_ptr_references(first,root,root/"evidence")
            self.assertEqual(first["meta"]["transaction_disclosure_relations"][0]["possible_ptr_transaction_ids"],["new"])
            self.assertEqual(len(audit["replacements"]),2)
            self.assertEqual(first,again);self.assertEqual(audit,again_audit)

    def test_conflict_and_unreferenced_cycle_in_combined_graph_rejected(self):
        for edge,error in [(("old","middle"),"conflicting successors"),(("new","old"),"cycle")]:
            with self.subTest(edge=edge),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);views,_=setup(root,(edge,));proof=wh_proof(root,[application()]);module=verifier(proof)
                data=candidate();data["meta"]["transaction_disclosure_relations"]=[]
                with patch.dict(sys.modules,{module.__name__:module}),patch(
                        "unison_snapshot.oge_native_replacements.verify_native_exhibit_context",return_value=views):
                    with self.assertRaisesRegex(ValueError,error):
                        reconcile_annual_ptr_references(data,root,root/"evidence")

    def test_final_fact_presence_and_source_person_are_required_even_without_annual_reference(self):
        for kind in ("missing","different_person"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);proof=wh_proof(root,[application()]);module=verifier(proof);data=candidate()
                data["meta"]["transaction_disclosure_relations"]=[]
                if kind=="missing":data["transactions"].pop()
                else:data["transactions"][-1]["person_id"]="other"
                with patch.dict(sys.modules,{module.__name__:module}):
                    with self.assertRaisesRegex(ValueError,"current same-person fact"):
                        reconcile_annual_ptr_references(data,root,root/"evidence")

    def test_annual_relation_person_must_also_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);proof=wh_proof(root,[application()]);module=verifier(proof);data=candidate()
            data["transactions"][0]["person_id"]="different_annual_person"
            with patch.dict(sys.modules,{module.__name__:module}):
                with self.assertRaisesRegex(ValueError,"current same-person fact"):
                    reconcile_annual_ptr_references(data,root,root/"evidence")

    def test_identical_oge_and_wh_proof_recorded_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);views,_=setup(root);item=deepcopy(views["0"]["native_exhibit_replacement_applications"][0])
            item["prior_fact"]={"person_id":"person"};proof=wh_proof(root,[item]);module=verifier(proof)
            with patch.dict(sys.modules,{module.__name__:module}),patch(
                    "unison_snapshot.oge_native_replacements.verify_native_exhibit_context",return_value=views):
                _,audit=reconcile_annual_ptr_references(candidate(),root,root/"evidence")
            self.assertEqual(len(audit["replacements"]),1)
