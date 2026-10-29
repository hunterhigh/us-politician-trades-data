"""Legacy nonempty extraction compatibility; synthetic wiring, not source gold."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_paper import (
    parse_paper_grid_observations, parse_paper_word_pages, _normalized_words,
    _legacy_grid_slot_bindings, _save_legacy_grid_input, replay_legacy_grid_extraction,
)
from unison_snapshot.senate_candidate import (
    build_senate_candidate, _legacy_grid_protection, _transaction, _person,
)
from tests.test_senate_paper_grid import inputs, Image
from tests.test_senate_paper import word, page
from tests.test_senate_candidate import identity, BASE, PARSER
from tests import test_senate_candidate as candidate_helpers


def legacy_inputs():
    args = inputs()
    args[3][0]["words"].extend([word("X", 1412, 2280), word("X", 1963, 2280)])
    prior = parse_paper_word_pages(args[0], args[1], [page(1), args[3][0]])
    args[2].update(legacy_extraction_failed=False, legacy_extraction_count=1)
    return args, prior


@unittest.skipIf(Image is None, "optional Senate image dependency unavailable")
class SenateLegacyGridTests(unittest.TestCase):
    def test_original_fingerprint_uniquely_binds_without_rekeying_grid(self):
        args, prior = legacy_inputs()
        extracted = parse_paper_grid_observations(*args, prior_extraction=prior)
        links = extracted["legacy_source_slot_correspondence"]
        self.assertEqual(len(links["bindings"]), 1)
        self.assertEqual(links["unbound_references"], [])
        self.assertEqual(links["bindings"][0]["legacy_extraction_id"], prior["transactions"][0]["extraction_id"])
        self.assertNotEqual(extracted["transactions"][0]["extraction_id"], prior["transactions"][0]["extraction_id"])
        prior["source_sha256"] = "f" * 64
        with self.assertRaisesRegex(SenateEfdError, "saved source extraction"):
            parse_paper_grid_observations(*args, prior_extraction=prior)

    def test_duplicate_equal_fields_do_not_resolve_source_slot(self):
        args, prior = legacy_inputs()
        extracted = parse_paper_grid_observations(*args, prior_extraction=prior)
        words = _normalized_words(args[3][0])
        repeated = [{**row, "top": row["top"] + 200} for row in words]
        observation = deepcopy(args[2])
        slot = deepcopy(observation["slots"][0])
        slot.update(grid_slot=2, row_bounds_px=[2436, 2570])
        observation["slots"].append(slot)
        rows = deepcopy(extracted["transactions"])
        second = deepcopy(rows[0])
        second["source_slot"]["grid_slot"] = 2
        second["extraction_id"] = "senate-ptr:" + "e" * 24
        links = _legacy_grid_slot_bindings(prior, {2: words + repeated}, observation,
                                           rows + [second], "f" * 64)
        self.assertEqual(links["bindings"], [])
        self.assertEqual(links["unbound_references"][0]["reason"], "legacy_source_anchor_not_unique")

    def test_reference_does_not_grant_formal_status_or_override_changed_base(self):
        args, prior = legacy_inputs()
        extracted = parse_paper_grid_observations(*args, prior_extraction=prior)
        person = identity(args[0]["document_id"])
        formal, reasons = _transaction(prior["transactions"][0], prior, person)
        self.assertEqual(reasons, [])
        self.assertEqual(_legacy_grid_protection(extracted, person, BASE)["bound"], {})
        base = {**BASE, "transactions": [formal]}
        protected = _legacy_grid_protection(extracted, person, base)
        self.assertEqual(list(protected["bound"].values()), [formal])
        changed = deepcopy(base)
        changed["transactions"][0]["asset_name"] += " changed"
        protected = _legacy_grid_protection(extracted, person, changed)
        self.assertEqual(protected["bound"], {})
        self.assertEqual(len(protected["unbound"]), 1)

    def test_candidate_preserves_exact_base_and_blocks_unbound_page(self):
        args, prior = legacy_inputs()
        extracted = parse_paper_grid_observations(*args, prior_extraction=prior)
        person = identity(args[0]["document_id"])
        formal, _ = _transaction(prior["transactions"][0], prior, person)
        for variant in ("bound", "unbound", "no_base"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                item = deepcopy(extracted)
                if variant == "unbound":
                    item["legacy_source_slot_correspondence"]["bindings"] = []
                item["parser_version"] = PARSER
                base = deepcopy(BASE)
                if variant != "no_base":
                    base["transactions"], base["people"] = [formal], [_person(person)]
                state = candidate_helpers.SenateCandidateTests().fixture(root, [person], [item])
                # Source replay itself has a separate real image/hash test below.
                with patch("unison_snapshot.senate_paper.replay_legacy_grid_extraction"):
                    result, audit = build_senate_candidate(root, state, base, evidence_root=root)
                self.assertEqual(len(result["transactions"]), 1)
                if variant == "no_base":
                    self.assertEqual(result["transactions"][0]["id"], item["transactions"][0]["extraction_id"])
                else:
                    self.assertEqual(result["transactions"], [formal])
                if variant == "unbound":
                    self.assertEqual(audit["retained_unbound_legacy_formal_count"], 1)
                    self.assertIn("legacy_formal_possible_source_overlap", audit["quarantined_rows"][0]["reasons"])
                    self.assertEqual(audit["qualified_rows"], [])
                else:
                    with patch("unison_snapshot.senate_paper.replay_legacy_grid_extraction"):
                        rerun, rerun_audit = build_senate_candidate(root, state, result, evidence_root=root)
                    self.assertEqual(rerun["transactions"], result["transactions"])
                    self.assertEqual(rerun_audit["retained_unbound_legacy_formal_count"], 0)

    def test_legacy_compatibility_needs_real_source_replay(self):
        args, prior = legacy_inputs()
        extracted = parse_paper_grid_observations(*args, prior_extraction=prior)
        extracted["parser_version"] = PARSER
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = candidate_helpers.SenateCandidateTests().fixture(
                root, [identity(args[0]["document_id"])], [extracted])
            with self.assertRaisesRegex(SenateEfdError, "archived source replay"):
                build_senate_candidate(root, state, deepcopy(BASE))

    def test_saved_replay_rejects_correspondence_and_input_tampering(self):
        args, prior = legacy_inputs()
        for listed in args[1]["pages"]:
            listed["archive_path"] = f"pages/{listed['page_number']}/{listed['sha256']}.gif"
        extracted = parse_paper_grid_observations(*args, prior_extraction=prior)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def save(relative, value):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            save("status/senate_efd.json", {"catalog_sha256": "c" * 64})
            save("senate_efd/discoveries/" + "c" * 64 + ".json", {"reports": [args[0]]})
            save(f"senate_efd/paper_pages/{args[0]['document_id']}/manifest.json", args[1])
            path = root / args[1]["pages"][1]["archive_path"]
            path.parent.mkdir(parents=True)
            path.write_bytes(args[4][2])
            extracted["grid_replay_input"] = _save_legacy_grid_input(root, args[0], args[2], args[3], prior)
            replay_legacy_grid_extraction(extracted, root, root)
            corrupted = deepcopy(extracted)
            corrupted["legacy_source_slot_correspondence"]["bindings"][0]["grid_extraction_id"] = "wrong"
            with self.assertRaisesRegex(SenateEfdError, "source replay"):
                replay_legacy_grid_extraction(corrupted, root, root)
            (root / extracted["grid_replay_input"]["path"]).write_bytes(b"{}")
            with self.assertRaisesRegex(SenateEfdError, "hash differs"):
                replay_legacy_grid_extraction(extracted, root, root)


if __name__ == "__main__":
    unittest.main()
