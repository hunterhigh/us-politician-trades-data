import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "audit_trump_candidate_transition",
    ROOT / "scripts/audit_trump_candidate_transition.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def candidate(*, added=False, keep_old=True, added_transaction=False):
    old = {"id": script.PUBLISHED_TRUMP_HOLDING_ID,
           "person_id": script.TRUMP_PERSON_ID, "source_id": "oge",
           "verification_status": "official_matched"}
    new = {"id": "wh-annual:new", "person_id": script.TRUMP_PERSON_ID,
           "source_id": "oge", "verification_status": "official_matched"}
    return {
        "meta": {"is_demo": False},
        "people": [{"id": script.TRUMP_PERSON_ID}],
        "transactions": [{"id": "tx:1"}] + ([{
            "id": "wh-annual-tx:new", "person_id": script.TRUMP_PERSON_ID,
            "source_id": "oge", "verification_status": "official_matched"}]
            if added_transaction else []),
        "reported_holdings": ([old] if keep_old else []) + ([new] if added else []),
        "security_market_data": [],
        "source_health": [{"source_id": "oge"}],
    }


class TrumpCandidateTransitionTests(unittest.TestCase):
    def test_additive_official_holding_passes(self):
        before, after = candidate(), candidate(added=True)
        result = script.audit_transition(before, after, before, after)
        self.assertEqual(result["added_holding_count"], 1)
        self.assertTrue(result["published_trump_holding_retained"])

    def test_additive_official_transaction_passes(self):
        before, after = candidate(), candidate(added_transaction=True)
        result = script.audit_transition(before, after, before, after)
        self.assertEqual(result["added_transaction_count"], 1)

    def test_removal_or_invalid_transaction_change_fails(self):
        before = candidate()
        with self.assertRaisesRegex(ValueError, "removed holdings"):
            script.audit_transition(before, candidate(keep_old=False),
                                    before, candidate(keep_old=False))
        after = candidate()
        after["transactions"].append({"id": "tx:2"})
        with self.assertRaisesRegex(ValueError, "delta does not match"):
            script.audit_transition(before, after, before, candidate())


if __name__ == "__main__":
    unittest.main()
