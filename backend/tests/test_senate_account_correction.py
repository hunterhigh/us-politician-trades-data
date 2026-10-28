"""Synthetic source replay and real Git/CLI contracts, not recognition gold."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from unison_snapshot.fact_policy.source_objects import InventoryError
from unison_snapshot.fact_policy.publication_delta import build_publication_delta
from unison_snapshot.fact_policy.senate_account_correction import verified_account_corrections


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture():
    from PIL import Image
    from tests.test_senate_paper_grid import inputs
    from tests.test_senate_paper_accounts import spacing
    from tests.test_senate_paper import word
    from tests.test_senate_candidate import identity, ROSTER_SHA, CONGRESS_ROSTER_SHA
    from unison_snapshot.senate_paper import parse_paper_grid_observations, _grid_json_bytes
    from unison_snapshot.senate_paper_observation import _source_report_relations
    from unison_snapshot.senate_reports import parse_paper_viewer_manifest, REPORT_ARCHIVE_SCHEMA
    from unison_snapshot.senate_candidate import _transaction
    from unison_snapshot.senate_slots_shadow import classify_slot, TYPE_CELLS, AMOUNT_CELLS
    report, _, observation, pages, images = inputs()
    tiny, request, tiny_words = spacing()
    image = Image.open(BytesIO(images[2])).convert("RGB")
    image.paste(tiny, (640, 2090))
    target = {**tiny_words[0], "left": 650., "top": 2100.}
    request.update(original_word=target, source_box_px=[650,2100,930,2145])
    source = BytesIO(); image.save(source, format="GIF"); raw = source.getvalue()
    page_sha = hashlib.sha256(raw).hexdigest()
    # The source RGB replay is the archived GIF, which has identical black/white pixels.
    restored = Image.open(BytesIO(raw)).convert("RGB")
    pad = Image.new("RGB", (296,61), "white"); pad.paste(restored.crop((650,2100,930,2145)), (8,8))
    request["input_pixels_sha256"] = hashlib.sha256(pad.tobytes()).hexdigest()
    request["page_sha256"] = page_sha
    request["grid_slot"] = 1
    def crop(box):
        return {"box_px":box,"grayscale_sha256":hashlib.sha256(restored.convert("L").crop(tuple(box)).tobytes()).hexdigest()}
    heading = classify_slot({"page_number":2,"grid_slot":1,"row_bounds_px":[2070,2200],
        "asset_ocr_raw":"(S)AcmeLLC :","date_ocr_raw":"","asset_ink_sample_count":200,
        "row_crop":crop([525,2070,2920,2200]),"asset_crop":crop([590,2078,1385,2192]),
        "date_crop":crop([1650,2078,1940,2192])}, restored.convert("L"),
        calibrated_centers=([x for _,x in TYPE_CELLS],[x for _,x in AMOUNT_CELLS],(1650,1940)),calibration_required=True)
    heading["label"] = "heading_candidate"
    observation["slots"][0]["grid_slot"] = 2
    observation["slots"].insert(0,heading)
    observation.update(physical_grid_slot_count=2,label_counts={"heading_candidate":1,"data_observed":1})
    observation["pages"][0].update(page_sha256=page_sha,physical_grid_slot_count=2,label_counts=observation["label_counts"])
    pages[0].update(source_sha256=page_sha,account_spacing_observations=[request])
    pages[0]["words"] += [target,word(":",950,2100)]
    viewer = (''.join(f'<img class="filingImage" src="https://efd-media-public.senate.gov/media/2026/2/000/000/00000000{n}.gif"><strong>Page {n} of 2</strong>' for n in (1,2))).encode()
    digest = hashlib.sha256(viewer).hexdigest()
    metadata = {**report,"schema_version":REPORT_ARCHIVE_SCHEMA,"source_id":"senate_efd",
        "access_method":"paper_ptr","media_kind":"html","document_id":report["document_id"],
        "document_url":report["document_url"],"sha256":digest,"byte_length":len(viewer)}
    manifest = {**parse_paper_viewer_manifest(metadata,viewer),"evidence_complete":True}
    cover = BytesIO(); Image.new("L",(3400,4400),255).save(cover,format="GIF")
    evidence = {}
    for page,content in zip(manifest["pages"],(cover.getvalue(),raw)):
        sha = hashlib.sha256(content).hexdigest()
        page.update(sha256=sha,archive_path=f'senate_efd/paper_pages/{report["document_id"]}/{page["page_number"]}/{sha}.gif')
        evidence[page["archive_path"]] = content
    manifest["manifest_sha256"] = hashlib.sha256(encoded(manifest)).hexdigest()
    observation.update(entrypoint_source_sha256=digest,manifest_sha256=manifest["manifest_sha256"])
    observation["source_report_ledger"] = _source_report_relations(observation)
    extraction = parse_paper_grid_observations(report,manifest,observation,pages,{2:raw})
    saved = _grid_json_bytes({"schema_version":"senate-paper-grid-replay/v1","report":report,
        "observation":observation,"cached_word_pages":pages,"prior_extraction":None})
    sha = hashlib.sha256(saved).hexdigest(); replay_path=f"senate_efd/paper_grid_inputs/{sha}.json"
    extraction["grid_replay_input"]={"path":replay_path,"sha256":sha}
    identity_row = identity(report["document_id"])
    fact,reasons = _transaction(extraction["transactions"][0],extraction,identity_row)
    assert not reasons, reasons
    proof = extraction["transactions"][0]["account_heading_source_slot"]["spacing_interpretation"]
    prior = {**fact,"asset_name":fact["asset_name"].replace(proof["text"],proof["original_word"]["text"])}
    catalog="f"*64
    status={"report_parser_version":extraction["parser_version"],"catalog_sha256":catalog,
        "identity_roster_sha256":ROSTER_SHA,"identity_congress_roster_sha256":CONGRESS_ROSTER_SHA}
    binding=hashlib.sha256(f"{ROSTER_SHA}:{CONGRESS_ROSTER_SHA}".encode()).hexdigest()
    identities={"schema_version":"senate-efd-identities/v1","source_id":"senate_efd","catalog_sha256":catalog,
        "roster_sha256":ROSTER_SHA,"congress_roster_sha256":CONGRESS_ROSTER_SHA,"identities":[identity_row]}
    review={"status/senate_efd.json":encoded(status),f"senate_efd/discoveries/{catalog}.json":encoded({"reports":[report]}),
        replay_path:saved,f"senate_efd/identities/{catalog}/{binding}.json":encoded(identities),
        f'senate_efd/extractions/{report["document_id"]}/{digest}/{extraction["parser_version"]}.json':encoded(extraction)}
    evidence.update({f'senate_efd/paper_pages/{report["document_id"]}/manifest.json':encoded(manifest),
        f'senate_efd/reports/{report["document_id"]}/{digest}.html':viewer,
        f'senate_efd/reports/{report["document_id"]}/{digest}.metadata.json':encoded(metadata)})
    return prior,fact,review,evidence


class SenateAccountCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=fixture()

    def verify(self, data=None, **overrides):
        prior,fact,review,evidence=deepcopy(data or self.data)
        args=dict(old={prior["id"]:prior},current={fact["id"]:fact},repo="unused",
            evidence_commit="e"*40,review_commit="b"*40,
            read_object=lambda repo,commit,path: (evidence if commit=="e"*40 else review)[path])
        args.update(overrides)
        return verified_account_corrections(**args)

    def test_replays_pixels_and_saved_words_without_receipt(self):
        result=self.verify()
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["changed_fields"],["asset_name"])
        self.assertEqual(result[0]["new_id"],result[0]["old_id"])

    def test_exact_previous_fact_and_no_other_field_changes(self):
        for field,value in (("asset_name","Wrong (Account: (S)AcmeLLC :)"),("amount_low",1),("person_id","other")):
            data=deepcopy(self.data);data[0][field]=value
            if field=="asset_name":
                with self.assertRaises(InventoryError):self.verify(data)
            else:self.assertEqual(self.verify(data),[])
        data=deepcopy(self.data);data[1]["asset_name"]=data[1]["asset_name"].replace("Acme","Acne")
        with self.assertRaises(InventoryError):self.verify(data)
        data=deepcopy(self.data);data[1]["id"]="senate-ptr:"+"0"*24
        self.assertEqual(self.verify(data),[])

    def test_wrong_commits_source_pixels_saved_input_and_versions_fail(self):
        for index,token in ((2,"paper_grid_inputs"),(3,".gif"),(3,"manifest.json"),(2,"extractions")):
            data=deepcopy(self.data)
            path=next(path for path in data[index] if token in path)
            data[index][path]+=b"corruption"
            with self.subTest(path=path),self.assertRaises(InventoryError):self.verify(data)
        with self.assertRaises(InventoryError):self.verify(review_commit="invalid")
        with self.assertRaises(InventoryError):self.verify(evidence_commit="d"*40)

    def test_receipt_cannot_authorize_other_corrections_or_wrong_source(self):
        from unittest.mock import patch
        prior,fact,_,_=deepcopy(self.data)
        current={"meta":{"is_demo":False,"data_cutoff_at":"2026-09-17"},"people":[],
            "transactions":[fact],"reported_holdings":[],"security_market_data":[],"source_health":[]}
        old={"people":[],"transactions":[prior],"reported_holdings":[]}
        sources={name:{**current,"transactions":[fact] if name=="senate_efd" else []}
            for name in ("house_clerk","oge","senate_efd")}
        proof=self.verify()[0]
        transitions={"schema_version":"pipeline-fact-transitions/v1","from_main_commit":"a"*40,
            "to_review_commit":"b"*40,"evidence_commit":"e"*40,"records":[proof]}
        def gate():
            return build_publication_delta(previous_board=old,unified=current,prepared=current,sources=sources,
                transitions=transitions,main_commit="a"*40,review_commit="b"*40,evidence_commit="e"*40,repo="unused")
        # Boundary test uses the independently replayed proof above, not a receipt as verification.
        with patch("unison_snapshot.fact_policy.publication_delta.verified_account_corrections",return_value=[proof]):
            result=gate();self.assertEqual(len(result["changes"]),1)
            for field,value in (("source_sha256","0"*64),("new_id","other"),
                                ("changed_fields",["asset_name","amount_low"]),
                                ("evidence_url","https://efdsearch.senate.gov/wrong")):
                transitions["records"]=[{**proof,field:value}]
                with self.subTest(field=field),self.assertRaises(InventoryError):gate()
        transitions["records"]=[proof]
        with patch("unison_snapshot.fact_policy.publication_delta.verified_account_corrections",return_value=[]):
            with self.assertRaises(InventoryError):gate()

    def test_review_identity_dates_and_saved_parser_tampering_fail(self):
        for token,mutate in (("discoveries",lambda doc:doc["reports"][0].update(portal_listed_date="2026-09-18")),
                             ("identities",lambda doc:doc["identities"][0].update(person_id="other")),
                             ("extractions",lambda doc:doc.update(parser_version="forged"))):
            data=deepcopy(self.data);path=next(path for path in data[2] if token in path)
            doc=json.loads(data[2][path]);mutate(doc);data[2][path]=encoded(doc)
            with self.subTest(token=token),self.assertRaises(InventoryError):self.verify(data)

    def test_normal_cli_reads_real_pinned_git_blobs_and_blocks_tampering(self):
        prior,fact,review,evidence=deepcopy(self.data)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);repo=root/"repo";repo.mkdir()
            def git(*args):
                return subprocess.check_output(["git","-C",str(repo),*args],stderr=subprocess.STDOUT).decode().strip()
            git("init","-q");git("config","user.email","contract@example.invalid");git("config","user.name","Contract")
            def commit(files):
                for path,raw in files.items():
                    target=repo/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
                git("add",".");git("commit","-qm","synthetic contract");return git("rev-parse","HEAD")
            evidence_commit=commit(evidence);review_commit=commit(review)
            meta={"is_demo":False,"data_cutoff_at":"2026-09-17T23:59:59Z"}
            old={"people":[],"transactions":[prior],"reported_holdings":[]}
            current={"meta":meta,"people":[],"transactions":[fact],"reported_holdings":[],"security_market_data":[],"source_health":[]}
            sources=root/"sources";sources.mkdir()
            for name in ("house_clerk","oge","senate_efd"):
                (sources/f"{name}-current.json").write_bytes(encoded({**current,"transactions":[fact] if name=="senate_efd" else []}))
            for name,value in (("old",old),("current",current)):(root/f"{name}.json").write_bytes(encoded(value))
            output=root/"delta.json"
            cmd=[sys.executable,"-m","unison_snapshot.fact_policy.publication_delta","--previous-board",str(root/"old.json"),
                "--unified",str(root/"current.json"),"--prepared",str(root/"current.json"),"--source-root",str(sources),
                "--output",str(output),"--repo",str(repo),"--evidence-commit",evidence_commit,"--review-commit",review_commit]
            result=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            delta=json.loads(output.read_bytes());self.assertEqual(delta["counts"]["transactions"]["corrected"],1)
            self.assertEqual(delta["source_verified_correction_count"],1)
            old["transactions"][0]["amount_low"]=1;(root/"old.json").write_bytes(encoded(old));output.unlink()
            result=subprocess.run(cmd,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse(output.exists())


if __name__=="__main__":unittest.main()
