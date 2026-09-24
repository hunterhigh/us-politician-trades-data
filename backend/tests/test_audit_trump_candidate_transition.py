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


def candidate(*, added=False, added_transaction=None, keep_old=True,
              change_transaction=False, transaction_person=None):
    old = {"id": script.PUBLISHED_TRUMP_HOLDING_ID,
           "person_id": script.TRUMP_PERSON_ID, "source_id": "oge",
           "verification_status": "official_matched"}
    new = {"id": "wh-annual:new", "person_id": script.TRUMP_PERSON_ID,
           "source_id": "oge", "verification_status": "official_matched"}
    transaction = {"id": "tx:1"}
    if change_transaction:
        transaction["changed"] = True
    transactions = [transaction]
    if added_transaction:
        identity = ("wh-annual-tx:new" if added_transaction == "annual" else
                    "oge-278t:" + "a" * 24)
        transactions.append({
            "id": identity,
            "person_id": transaction_person or script.TRUMP_PERSON_ID,
            "source_id": "oge", "verification_status": "official_matched",
            "filing_id": "wh-url:fixed",
            "source_url": "https://www.whitehouse.gov/wp-content/uploads/2025/08/trump.pdf",
        })
    return {
        "meta": {"is_demo": False},
        "people": [{"id": script.TRUMP_PERSON_ID}],
        "transactions": transactions,
        "reported_holdings": ([old] if keep_old else []) + ([new] if added else []),
        "security_market_data": [],
        "source_health": [{"source_id": "oge"}],
    }


class TrumpCandidateTransitionTests(unittest.TestCase):
    def test_allows_only_first_annual_ticker_upgrade_in_both_layers(self):
        before, after = candidate(), candidate()
        annual = {
            "id": "wh-annual-tx:" + "a" * 24,
            "ticker": None, "ticker_mapping_basis": None,
            "asset_name": "KRAFT HEINZ CO",
        }
        before["transactions"].append(dict(annual))
        after["transactions"].append(dict(
            annual, ticker="KHC", ticker_mapping_basis="alpaca_unique_asset_name"))
        result = script.audit_transition(before, after, before, after)
        self.assertEqual(result["annual_ticker_upgrade_count"], 1)
        changed = candidate()
        changed["transactions"].append(dict(
            annual, asset_name="Changed", ticker="KHC",
            ticker_mapping_basis="alpaca_unique_asset_name"))
        with self.assertRaisesRegex(ValueError, "removed or changed existing transactions"):
            script.audit_transition(before, changed, before, changed)

    def test_additive_official_holding_passes(self):
        before, after = candidate(), candidate(added=True)
        result = script.audit_transition(before, after, before, after)
        self.assertEqual(result["added_holding_count"], 1)
        self.assertTrue(result["published_trump_holding_retained"])

    def test_removal_or_existing_transaction_change_fails(self):
        before = candidate()
        with self.assertRaisesRegex(ValueError, "removed holdings"):
            script.audit_transition(before, candidate(keep_old=False),
                                    before, candidate(keep_old=False))
        with self.assertRaisesRegex(ValueError, "removed or changed existing transactions"):
            script.audit_transition(before, candidate(change_transaction=True),
                                    before, candidate(change_transaction=True))

    def test_additive_official_trump_transactions_pass(self):
        before = candidate()
        for kind in ("annual", "278t"):
            after = candidate(added_transaction=kind)
            result = script.audit_transition(before, after, before, after)
            self.assertEqual(result["added_transaction_count"], 1)
            self.assertTrue(result["oge_transaction_delta_matches_unified"])

    def test_non_trump_or_mismatched_oge_transaction_delta_fails(self):
        before = candidate()
        after = candidate(added_transaction="278t", transaction_person="oge:other")
        with self.assertRaisesRegex(ValueError, "non-Trump or non-official transaction"):
            script.audit_transition(before, after, before, after)
        with self.assertRaisesRegex(ValueError, "transaction delta"):
            script.audit_transition(before, candidate(added_transaction="278t"),
                                    before, before)
        mismatched = candidate(added_transaction="278t")
        mismatched_oge = candidate(added_transaction="278t")
        mismatched_oge["transactions"][-1]["asset_name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "differ from the OGE source"):
            script.audit_transition(before, mismatched, before, mismatched_oge)


if __name__ == "__main__":
    unittest.main()
