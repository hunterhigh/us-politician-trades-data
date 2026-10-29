"""Guard composition tests; mocked positives do not prove a font/paint profile."""
from copy import deepcopy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_house_revision_source_context as context_fixture
from unison_snapshot.house_revision_scope_guard import derive_house_revision_scope
from unison_snapshot.house_revision_source_scope import VERSION as SOURCE_VERSION


PERSON = context_fixture.PERSON


def mock_source_profile(document):
    """Controlled interface observation, deliberately not a source proof."""
    return {"schema_version": SOURCE_VERSION, "status": "observed_scope", "scope_complete": True,
        "document_id": document.document_id, "source_sha256": document.source_sha256, "reasons": [],
        "pages": [{"page_number": 1, "status": "observed", "declarations": [
            {"reported_transaction_id": row["reported_transaction_id"], "source_row_indices": [i],
             "source_character_indices": [], "word_indices": []}
            for i, row in enumerate(document.extraction["transactions"])]}]}


class HouseRevisionScopeGuardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = context_fixture.HouseRevisionSourceContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.inputs = [(deepcopy(self.fixture.qualifications[doc]), deepcopy(self.fixture.extractions[doc]))
            for doc in ("20000001", "20000002")]
        self.current = deepcopy(self.inputs[0][0]["transactions"])

    def derive(self, inputs=None, profile=mock_source_profile):
        with patch("unison_snapshot.house_revision_source_scope.inspect_native_house_revision_source", side_effect=profile) as spy:
            result = derive_house_revision_scope(self.inputs if inputs is None else inputs, self.current,
                prior_transactions=self.current, evidence_root=self.fixture.evidence, review_root=self.fixture.review)
        return result, spy

    def test_mock_scope_only_exercises_complete_context_and_normal_q_projection_contract(self):
        before = deepcopy((self.inputs, self.current))
        result, spy = self.derive()
        self.assertEqual(result["status"], "observed_scope")
        self.assertEqual(result["allowed_groups"], [[PERSON, "2000140446"]])
        self.assertEqual(result["people"][PERSON]["allowed_official_ids"], ["2000140446"])
        self.assertFalse(result["relationship_proven"])
        self.assertFalse(result["source_context"]["scope_complete"])
        self.assertEqual(spy.call_count, 2)
        self.assertEqual((self.inputs, self.current), before)
        # Mapping access and exported audit are copies of immutable bytes.
        result["allowed_groups"].clear()
        result.audit()["people"].clear()
        self.assertEqual(result.get("allowed_groups"), [[PERSON, "2000140446"]])
        with self.assertRaises(TypeError):
            result["status"] = "passed"
        self.assertEqual(self.derive()[0].audit(), result.audit())

    def test_missing_duplicate_and_extra_selected_inputs_block_the_entire_person(self):
        missing = self.inputs[1:]
        duplicate = [*self.inputs, deepcopy(self.inputs[0])]
        extra = deepcopy(self.inputs[0])
        extra[1]["source"]["document_id"] = "99999999"
        for inputs, expected in ((missing, "selected_source_input_missing_or_duplicate"),
                (duplicate, "selected_source_input_missing_or_duplicate"),
                ([*self.inputs, extra], "caller_person_source_outside_original_context")):
            with self.subTest(expected=expected):
                result, _ = self.derive(inputs)
                self.assertEqual(result["allowed_groups"], [])
                self.assertTrue(any(expected in reason for reason in result["people"][PERSON]["reasons"]))

    def test_callers_full_q_or_x_difference_cannot_reuse_a_bound_original(self):
        for target in ("qualification", "extraction", "optional_field"):
            with self.subTest(target=target):
                supplied = deepcopy(self.inputs)
                if target == "qualification":
                    supplied[0][0]["transactions"][0]["amount_low"] += 1
                elif target == "extraction":
                    supplied[0][1]["transactions"][0]["asset_name"] = "Changed Fund"
                else:
                    supplied[0][1]["unexpected_null_field"] = None
                result, _ = self.derive(supplied)
                self.assertEqual(result["allowed_groups"], [])
                self.assertTrue(any("caller_input_differs_from_selected_original_cache" in reason
                    for reason in result["people"][PERSON]["reasons"]))

    def test_self_signed_cache_and_saved_passed_marker_fail_normal_qualification_replay(self):
        q, x = self.inputs[0]
        q["qualification"]["qualified_count"] = 99
        q["qualification"]["saved_scope_complete"] = True
        relative = f"2026/20000001/{x['source_sha256']}.json"
        context_fixture.write(self.fixture.review / "house_clerk/qualifications" / relative, q)
        result, _ = self.derive()
        self.assertEqual(result["allowed_groups"], [])
        self.assertIn("2026/20000001:qualification_differs_from_normal_replay", result["people"][PERSON]["reasons"])

    def test_unknown_or_wrong_bound_profile_and_missing_declaration_never_fall_back_to_cache(self):
        for kind in ("unknown", "wrong_sha", "missing_id", "missing_page"):
            def profile(document):
                value = mock_source_profile(document)
                if document.document_id == "20000002":
                    if kind == "unknown":
                        value.update(status="unknown", scope_complete=False, reasons=["source_paint_unknown"])
                    if kind == "wrong_sha":
                        value["source_sha256"] = "a" * 64
                    if kind == "missing_id":
                        value["pages"][0]["declarations"] = []
                    if kind == "missing_page":
                        value["pages"] = []
                return value
            with self.subTest(kind=kind):
                result, _ = self.derive(profile=profile)
                self.assertEqual(result["allowed_groups"], [])
                self.assertEqual(result["people"][PERSON]["status"], "unknown")

    def test_same_person_opaque_source_blocks_even_if_it_has_no_caller_input(self):
        self.fixture.rows += context_fixture.member("20000003")
        self.fixture.archive_index(self.fixture.rows, "2026-09-19T00:00:00Z")
        self.fixture.add_document("20000003", image_page=True, cache=False)
        before = deepcopy(self.current)
        result, spy = self.derive()
        self.assertEqual(result["allowed_groups"], [])
        self.assertEqual(spy.call_count, 0)
        self.assertTrue(any("opaque_source_cannot_prove_competitor_absence" in reason
            for reason in result["people"][PERSON]["reasons"]))
        self.assertEqual(self.current, before)

    def test_other_source_resolved_person_is_not_a_whole_financial_perfection_gate(self):
        extra = (b"<member><statedistrict>NY01</statedistrict><member-info><bioguideID>C000001</bioguideID>"
            b"<official-name>Ada Carter</official-name><party>D</party><state postal-code='NY'/></member-info></member>")
        xml = context_fixture.XML.replace(b"</members>", extra + b"</members>")
        date, members = context_fixture.parse_members(xml)
        context_fixture.archive_members(self.fixture.evidence, xml, {}, members, date, retrieved_at=context_fixture.TIME)
        self.fixture.archive_index(self.fixture.rows + context_fixture.member("20000003", first="Ada", last="Carter", district="NY01"),
            "2026-09-19T00:00:00Z")
        result, spy = self.derive()
        self.assertEqual(result["allowed_groups"], [[PERSON, "2000140446"]])
        self.assertEqual(spy.call_count, 2)

    def test_no_request_or_missing_roots_never_prove_a_chain(self):
        result, spy = self.derive(self.inputs[:1])
        self.assertEqual(result["status"], "not_requested")
        self.assertEqual(result["allowed_groups"], [])
        spy.assert_not_called()
        missing = derive_house_revision_scope(self.inputs, self.current, prior_transactions=self.current,
            evidence_root=None, review_root=None)
        self.assertEqual(missing["allowed_groups"], [])
        self.assertIn("controlled_source_roots_unavailable", missing["people"][PERSON]["reasons"])

    def test_bounded_input_and_audit_encoding_cannot_keep_allowed_groups(self):
        with patch("unison_snapshot.house_revision_scope_guard.MAX_OBJECT_BYTES", 1):
            result, _ = self.derive()
        self.assertEqual(result["allowed_groups"], [])
        self.assertTrue(any("qualification_original_replay_failed" in r for r in result["people"][PERSON]["reasons"]))
        with patch("unison_snapshot.house_revision_scope_guard.MAX_AUDIT_BYTES", 1):
            result, _ = self.derive()
        self.assertEqual(result["allowed_groups"], [])
        self.assertEqual(result["reasons"], ["scope_audit_byte_budget_exceeded"])


if __name__ == "__main__":
    unittest.main()
