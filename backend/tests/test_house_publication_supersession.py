"""Publication event consumption only; mocked verifiers are NOT source proof.

Original PDF/Git/font/ID-scope verification is tested separately. These tests
isolate dispatch, fixed-ref forwarding, ID accounting and atomic failure.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_pipeline_publication_delta as delta_fixtures
from unison_snapshot.pipeline_qa.migration_inventory import InventoryError, _differences
from unison_snapshot.pipeline_qa.publication_delta import build_publication_delta, main

MAIN, REVIEW, EVIDENCE = "a" * 40, "b" * 40, "c" * 40
SOURCE_SHA = "d" * 64
MODULE = "unison_snapshot.pipeline_qa.publication_delta"


def fixture(*, extra_old=False, extra_new=False):
    previous, unified, prepared, sources = delta_fixtures.PublicationDeltaTests().fixture()
    person = {"id": "house-person", "disclosure_authority": "house_clerk",
              "display_name": "Example Representative"}
    old = {"id": "house-old", "filing_id": "20000001", "person_id": person["id"],
           "source_id": "house_clerk", "source": "House Clerk",
           "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000001.pdf",
           "verification_status": "official_matched", "owner": "Self",
           "asset_name": "Example", "instrument_type": "Stock", "direction": "Purchase",
           "transaction_date": "2026-09-01", "filed_at": "2026-09-10T00:00:00Z",
           "amount_low": 1001, "amount_high": 15000}
    new = {**old, "id": "house-new", "filing_id": "20000002",
           "source_url": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000002.pdf",
           "filed_at": "2026-09-12T00:00:00Z", "amount_low": 15001, "amount_high": 50000}
    previous["people"].append(deepcopy(person))
    previous["transactions"] = [old]
    for board in (unified, prepared):
        board["people"].append(deepcopy(person))
        board["transactions"] = [deepcopy(new)]
    sources["house_clerk"]["people"] = [deepcopy(person)]
    sources["house_clerk"]["transactions"] = [deepcopy(new)]
    if extra_old:
        previous["transactions"].append({**old, "id": "house-old-b", "asset_name": "Another"})
    if extra_new:
        another = {**new, "id": "house-new-b", "asset_name": "Unrelated new fact"}
        for board in (unified, prepared, sources["house_clerk"]):
            board["transactions"].append(deepcopy(another))
    return previous, unified, prepared, sources


def event(values, old_id="house-old", new_id="house-new"):
    old = next(r for r in values[0]["transactions"] if r["id"] == old_id)
    current = next(r for r in values[2]["transactions"] if r["id"] == new_id)
    return {"entity": "transactions", "kind": "superseded", "old_id": old_id, "new_id": new_id,
            "reason": "official_amendment", "evidence_url": current["source_url"],
            "source_sha256": SOURCE_SHA, "changed_fields": _differences(old, current)}


def transitions(records):
    return {"schema_version": "pipeline-fact-transitions/v1", "from_main_commit": MAIN,
            "to_review_commit": REVIEW, "evidence_commit": EVIDENCE, "records": records}


def run(values, records=None, **overrides):
    old, unified, prepared, sources = values
    kwargs = {"previous_board": old, "unified": unified, "prepared": prepared, "sources": sources,
              "main_commit": MAIN, "review_commit": REVIEW, "evidence_commit": EVIDENCE,
              "repo": Path("fixed-contract-repository"), "transitions": None if records is None else transitions(records)}
    kwargs.update(overrides)
    return build_publication_delta(**kwargs)


class HousePublicationSupersessionTests(unittest.TestCase):
    def test_automatic_amount_revision_is_one_supersession_not_a_new_or_rekey(self):
        values = fixture(); checked = event(values)
        with patch(MODULE + ".verified_house_revision_supersessions", return_value=[checked]) as verifier:
            report = run(values)
        self.assertEqual(report["changes"], [checked])
        self.assertEqual(report["counts"]["transactions"],
                         {"previous": 1, "prepared": 1, "unchanged": 0, "new": 0, "superseded": 1})
        self.assertEqual(report["source_verified_house_supersession_count"], 1)
        self.assertNotIn("rekey", report["counts"]["transactions"])
        self.assertIn("native_ID_inventory", report["house_supersession_verification_level"])
        self.assertEqual(verifier.call_count, 1)
        args = verifier.call_args.kwargs
        self.assertEqual((args["main_commit"], args["review_commit"], args["evidence_commit"]),
                         (MAIN, REVIEW, EVIDENCE))
        self.assertEqual(args["previous_people"], values[0]["people"])
        self.assertEqual(args["old"], {r["id"]: r for r in values[0]["transactions"]})
        self.assertEqual(args["current"], {r["id"]: r for r in values[2]["transactions"]})

    def test_matching_explicit_and_automatic_event_are_emitted_and_counted_once(self):
        values = fixture(); checked = event(values)
        before = deepcopy(values)
        with patch(MODULE + ".verified_house_revision_supersessions", return_value=[checked]):
            first = run(values, [deepcopy(checked)])
            second = run(values, [deepcopy(checked)])
        self.assertEqual(first, second)
        self.assertEqual(first["changes"], [checked])
        self.assertEqual(first["counts"]["transactions"]["superseded"], 1)
        self.assertEqual(first["source_verified_house_supersession_count"], 1)
        self.assertEqual(values, before)

    def test_only_replacement_target_is_excluded_from_new_count(self):
        values = fixture(extra_new=True); checked = event(values)
        with patch(MODULE + ".verified_house_revision_supersessions", return_value=[checked]):
            report = run(values)
        self.assertEqual(report["counts"]["transactions"]["new"], 1)
        self.assertEqual(report["counts"]["transactions"]["superseded"], 1)
        self.assertEqual([r for r in report["changes"] if r["kind"] == "new"],
                         [{"entity": "transactions", "kind": "new", "new_id": "house-new-b", "source_id": "house_clerk"}])

    def test_no_source_verifier_means_amount_and_ID_change_is_unexplained(self):
        values = fixture()
        with patch(MODULE + ".verified_house_revision_supersessions", return_value=[]):
            with self.assertRaisesRegex(InventoryError, "unexplained removal"):
                run(values)
            with self.assertRaisesRegex(InventoryError, "supersession needs matching"):
                run(values, [event(values)])

    def test_same_ID_amount_change_still_requires_its_own_correction_verifier(self):
        values = fixture()
        original = values[0]['transactions'][0]
        updated = {**original, 'amount_low': 15001, 'amount_high': 50000}
        for board in (values[1], values[2], values[3]['house_clerk']):
            board['transactions'] = [deepcopy(updated)]
        # Actual supersession verifier returns no edge when there is no
        # removed/added source-ID pair; no mock grants a same-ID correction.
        with self.assertRaisesRegex(InventoryError, 'unexplained removal or field change'):
            run(values)

    def test_automatic_verified_edge_must_still_bind_actual_removed_and_new_ids(self):
        # Source-verifier output contract defense, not a claim that an
        # untrusted user can replace the real verifier with this mock.
        values = fixture()
        checked = event(values)
        for update in ({'new_id': 'not-in-current'}, {'kind': 'new'}, {'entity': 'people'},
                       {'changed_fields': ['id']}):
            with self.subTest(update=update), patch(MODULE + '.verified_house_revision_supersessions',
                                                    return_value=[{**checked, **update}]):
                with self.assertRaises(InventoryError):
                    run(values)
        values = fixture(extra_old=True)
        retained = deepcopy(values[0]['transactions'][1])
        for board in (values[1], values[2], values[3]['house_clerk']):
            board['transactions'] = [deepcopy(retained)]
        checked = event(values, new_id='house-old-b')
        with patch(MODULE + '.verified_house_revision_supersessions', return_value=[checked]):
            with self.assertRaises(InventoryError):
                run(values)

    def test_source_free_self_signed_event_with_actual_verifier_is_rejected(self):
        values = fixture()
        # No mock: an official URL/hash and a transition label cannot provide
        # the missing fixed source repository, even with all three ref labels.
        with self.assertRaisesRegex(InventoryError, "fixed main, review and evidence"):
            run(values, [event(values)], repo=None)

    def test_explicit_event_must_exactly_match_verified_projection(self):
        values = fixture(); checked = event(values)
        mutations = [{"new_id": "other"}, {"source_sha256": "e" * 64},
                     {"evidence_url": "https://disclosures-clerk.house.gov/other.pdf"},
                     {"reason": "duplicate_resolution"}, {"changed_fields": ["id"]}]
        for update in mutations:
            with self.subTest(update=update), patch(MODULE + ".verified_house_revision_supersessions", return_value=[checked]):
                with self.assertRaisesRegex(InventoryError, "supersession needs matching"):
                    run(values, [{**checked, **update}])

    def test_amount_changes_cannot_be_relabeled_rekey_or_same_ID_correction(self):
        values = fixture(); checked = event(values)
        with patch(MODULE + ".verified_house_revision_supersessions", return_value=[checked]):
            with self.assertRaisesRegex(InventoryError, "rekey must preserve"):
                run(values, [{**checked, "kind": "rekey"}])
            with self.assertRaisesRegex(InventoryError, "correction needs"):
                run(values, [{**checked, "kind": "corrected"}])
        # The preexisting holdings rekey rule must still reject amount drift.
        values = delta_fixtures.PublicationDeltaTests().fixture()
        replacement = {**values[0]["reported_holdings"][0], "id": "new-holding",
                       "filing_id": "new-filing", "source_url": "https://www.whitehouse.gov/new.pdf", "value_low": 9000}
        for board in (values[1], values[2], values[3]["oge"]):
            board["reported_holdings"] = [deepcopy(replacement)]
        decision = {"entity": "reported_holdings", "kind": "rekey", "old_id": "old-holding", "new_id": "new-holding",
                    "reason": "official_index_replacement", "evidence_url": replacement["source_url"], "source_sha256": SOURCE_SHA}
        with patch(MODULE + "._archive") as archive:
            with self.assertRaisesRegex(InventoryError, "rekey must preserve"):
                run(values, [decision])
            archive.assert_not_called()

    def test_explicit_duplicate_old_ID_and_automatic_duplicate_old_ID_are_rejected(self):
        for different_targets in (False, True):
            values = fixture(extra_new=different_targets)
            first = event(values)
            second = event(values, new_id="house-new-b") if different_targets else deepcopy(first)
            with self.subTest(different_targets=different_targets):
                with patch(MODULE + ".verified_house_revision_supersessions", return_value=[first]):
                    with self.assertRaises(InventoryError):
                        run(values, [deepcopy(first), deepcopy(first)])
                with patch(MODULE + ".verified_house_revision_supersessions", return_value=[first, second]):
                    with self.assertRaises(InventoryError):
                        run(values)

    def test_explicit_automatic_and_mixed_duplicate_new_IDs_are_rejected(self):
        values = fixture(extra_old=True)
        first, second = event(values), event(values, old_id="house-old-b")
        for records in (None, [deepcopy(first)], [deepcopy(first), deepcopy(second)]):
            with self.subTest(records=records), patch(MODULE + ".verified_house_revision_supersessions", return_value=[first, second]):
                with self.assertRaises(InventoryError):
                    run(values, records)

    def test_fixed_ref_mismatch_and_source_alignment_still_precede_event_acceptance(self):
        values = fixture(); checked = event(values)
        with patch(MODULE + ".verified_house_revision_supersessions", return_value=[checked]):
            with self.assertRaisesRegex(InventoryError, "bind fixed commits"):
                run(values, [checked], transitions={**transitions([checked]), "to_review_commit": "f" * 40})
            values[3]["house_clerk"]["transactions"][0]["amount_low"] += 1
            with self.assertRaisesRegex(InventoryError, "source field conflict"):
                run(values)

    def test_normal_delta_CLI_failure_preserves_existing_output_bytes(self):
        values = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source_root = root / "sources"; source_root.mkdir()
            files = []
            for name, value in zip(("previous-board", "unified", "prepared"), values[:3]):
                path = root / (name + ".json"); path.write_text(json.dumps(value), encoding="utf-8")
                files += ["--" + name, str(path)]
            for name, value in values[3].items():
                (source_root / (name + "-current.json")).write_text(json.dumps(value), encoding="utf-8")
            output = root / "delta.json"; output.write_bytes(b"existing verified output\n")
            argv = ["publication-delta", *files, "--source-root", str(source_root), "--output", str(output),
                    "--repo", str(root), "--main-commit", MAIN, "--review-commit", REVIEW, "--evidence-commit", EVIDENCE]
            with patch("sys.argv", argv), patch(MODULE + ".verified_house_revision_supersessions",
                    side_effect=InventoryError("source scope replay rejected")):
                with self.assertRaisesRegex(InventoryError, "source scope replay rejected"):
                    main()
            self.assertEqual(output.read_bytes(), b"existing verified output\n")


if __name__ == "__main__":
    unittest.main()
