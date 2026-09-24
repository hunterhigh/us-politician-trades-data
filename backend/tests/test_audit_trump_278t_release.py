"""Release transition and frozen frontend gates for Trump 278-T rows."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "audit_trump_278t_release", ROOT / "scripts/audit_trump_278t_release.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


URL = script.TARGET_SOURCE_URL
TX_ID = "oge-278t:" + "a" * 24
HOLDING_ID = "wh-annual:" + "b" * 24


def transaction():
    return {
        "id": TX_ID, "filing_id": script.TARGET_DOCUMENT_ID,
        "person_id": script.TRUMP_PERSON_ID, "owner": "Self",
        "asset_name": "Microsoft Corp.", "ticker": "MSFT",
        "source_id": "oge", "source_url": URL,
        "filed_at": script.TARGET_FILED_AT,
        "verification_status": "official_matched",
    }


def candidate(*, added=False):
    return {
        "meta": {"is_demo": False},
        "people": [{"id": script.TRUMP_PERSON_ID, "display_name": "Donald Trump"}],
        "transactions": [transaction()] if added else [],
        "reported_holdings": [{
            "id": HOLDING_ID, "person_id": script.TRUMP_PERSON_ID,
            "source_id": "oge", "verification_status": "official_matched",
        }],
        "security_market_data": [],
        "source_health": [{"source_id": "oge"}],
    }


def conservation():
    rows = [{"extraction_id": TX_ID, "row_number": 1, "status": "promoted"}]
    rows.extend({"extraction_id": "oge-278t:" + f"{index:024x}",
                 "row_number": index, "status": "quarantined"}
                for index in range(2, 508))
    return {
        "schema_version": "whitehouse-278t-candidate-conservation/v1",
        "candidate_is_append_only": True,
        "source_row_conservation_complete": True,
        "existing_transaction_mutation_count": 0,
        "report_count": 1, "source_row_count": 507,
        "promoted_transaction_count": 1,
        "promoted_transaction_ids": [TX_ID], "quarantined_row_count": 506,
        "reports": [{
            "document_id": script.TARGET_DOCUMENT_ID, "source_url": URL,
            "source_sha256": script.TARGET_SOURCE_SHA256,
            "filed_at": script.TARGET_FILED_AT[:10],
            "source_row_count": 507, "promoted_count": 1, "quarantined_count": 506,
            "rows": rows,
        }],
    }


def frontend(after):
    common = {
        "meta": {"is_demo": False},
        "people": deepcopy(after["people"]),
        "transactions": deepcopy(after["transactions"]),
        "reported_holdings": deepcopy(after["reported_holdings"]),
        "security_market_data": [{"ticker": "MSFT"}],
        "source_health": deepcopy(after["source_health"]),
    }
    dashboard = deepcopy(common)
    dashboard["meta"]["selection_scope"] = {"mode": "dashboard", "key": None}
    search = deepcopy(common)
    search["meta"]["selection_scope"] = {"mode": "search", "key": None}
    person = deepcopy(common)
    person["meta"]["selection_scope"] = {
        "mode": "person", "key": script.TRUMP_PERSON_ID}
    ticker = deepcopy(common)
    ticker["meta"]["selection_scope"] = {"mode": "ticker", "key": "MSFT"}
    return {"dashboard": dashboard, "search": search,
            "person": person, "ticker": ticker}


class Trump278TReleaseAuditTests(unittest.TestCase):
    def test_existing_annual_transaction_may_gain_only_a_unique_ticker(self):
        annual = {
            "id": "wh-annual-tx:" + "c" * 24,
            "ticker": None, "ticker_mapping_basis": None,
            "asset_name": "KRAFT HEINZ CO",
        }
        before, after = candidate(), candidate(added=True)
        before["transactions"] = [deepcopy(annual)]
        upgraded = dict(annual, ticker="KHC",
                        ticker_mapping_basis="alpaca_unique_asset_name")
        after["transactions"] = [upgraded, *after["transactions"]]
        result = script.audit_release(before, after, before, after, conservation())
        self.assertEqual(result["annual_ticker_upgrade_count"], 1)

    def test_append_only_delta_and_four_frontend_modes_pass(self):
        before, after = candidate(), candidate(added=True)
        result = script.audit_release(
            before, after, before, after, conservation(), frontend(after))
        self.assertEqual(result["promoted_transaction_count"], 1)
        self.assertEqual(result["candidate_transaction_delta_count"], 1)
        self.assertEqual(result["reported_holding_delta_count"], 0)
        self.assertTrue(result["candidate_is_append_only"])
        self.assertTrue(result["frontend_verified"])
        self.assertEqual(result["frontend"]["ticker"], "MSFT")
        self.assertEqual(result["frontend"]["ticker_added_transaction_count"], 1)

    def test_previously_published_whitehouse_rows_are_retained_not_readded(self):
        old_id = "oge-278t:" + "9" * 24
        old = dict(transaction(), id=old_id, filing_id="wh-url:" + "8" * 24,
                   source_url="https://www.whitehouse.gov/wp-content/uploads/2025/06/Old.pdf")
        before, after = candidate(), candidate(added=True)
        before["transactions"] = [old]
        after["transactions"] = [old, *after["transactions"]]
        combined = conservation()
        combined["reports"].append({
            "document_id": old["filing_id"], "source_url": old["source_url"],
            "source_sha256": "8" * 64, "filed_at": "2025-06-01",
            "source_row_count": 1, "promoted_count": 1, "quarantined_count": 0,
            "rows": [{"extraction_id": old_id, "status": "promoted"}],
        })
        combined["report_count"] = 2
        combined["source_row_count"] = 508
        combined["promoted_transaction_count"] = 2
        combined["promoted_transaction_ids"] = sorted([old_id, TX_ID])
        result = script.audit_release(before, after, before, after, combined)
        self.assertEqual(result["promoted_transaction_ids"], [TX_ID])
        self.assertEqual(result["candidate_transaction_delta_count"], 1)

    def test_candidate_delta_must_equal_promoted_rows_in_both_layers(self):
        before, after = candidate(), candidate(added=True)
        changed = conservation()
        changed["promoted_transaction_ids"] = []
        with self.assertRaisesRegex(ValueError, "batch disposition counts"):
            script.audit_release(before, after, before, after, changed)
        with self.assertRaisesRegex(ValueError, "both candidate deltas"):
            script.audit_release(before, after, before, before, conservation())

    def test_existing_transactions_and_holdings_cannot_change(self):
        before, after = candidate(added=True), candidate(added=True)
        after["transactions"][0]["asset_name"] = "Changed Corp."
        with self.assertRaisesRegex(ValueError, "changed or removed an existing transaction"):
            script.audit_release(before, after, before, after, conservation())
        before, after = candidate(), candidate(added=True)
        after["reported_holdings"] = []
        with self.assertRaisesRegex(ValueError, "changed reported holdings"):
            script.audit_release(before, after, before, after, conservation())

    def test_frontend_must_show_added_row_in_every_required_view(self):
        before, after = candidate(), candidate(added=True)
        views = frontend(after)
        views["search"]["transactions"] = []
        with self.assertRaisesRegex(ValueError, "search readback differs"):
            script.audit_release(before, after, before, after, conservation(), views)
        views = frontend(after)
        views["ticker"]["transactions"] = []
        with self.assertRaisesRegex(ValueError, "ticker readback omits"):
            script.audit_release(before, after, before, after, conservation(), views)

    def test_first_batch_cannot_add_a_different_whitehouse_report(self):
        before, after = candidate(), candidate(added=True)
        other = conservation()
        other["reports"][0]["document_id"] = "wh-url:" + "e" * 24
        after["transactions"][0]["filing_id"] = other["reports"][0]["document_id"]
        with self.assertRaisesRegex(ValueError, "fixed source report"):
            script.audit_release(before, after, before, after, other)


if __name__ == "__main__":
    unittest.main()
