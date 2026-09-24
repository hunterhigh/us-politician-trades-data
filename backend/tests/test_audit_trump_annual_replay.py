import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "audit_trump_annual_replay", ROOT / "scripts/audit_trump_annual_replay.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def row(section="part2", **extra):
    return {"section": section, "page_number": extra.pop("page_number", 1),
            "row_number": extra.pop("row_number", "1"), **extra}


class TrumpReplayAuditTests(unittest.TestCase):
    def fixture(self):
        counts = script.EXPECTED_COUNTS
        part6_counts = script.EXPECTED_PART6_COUNTS
        part7_counts = script.EXPECTED_PART7_COUNTS
        part7_locator_offset = {
            "holdings": 0, "transactions": 0,
            "excluded": 20000, "quarantined": 30000}
        values = {}
        locator = 0
        for name, count in counts.items():
            rows = []
            for part6_index in range(part6_counts[name]):
                locator += 1
                value = row("part6", source_row_locator=f"p1-y{locator}",
                            account_scope=f"account-{locator % 3}")
                if (name == "holdings" and part6_index >=
                        script.EXPECTED_PART6_NUMERIC_HOLDING_COUNT):
                    value["row_number"] = f"ocr-p1-y{locator}"
                else:
                    value["row_number"] = str(locator)
                if name == "quarantined":
                    value["reasons"] = ["fixture"]
                rows.append(value)
            for index in range(part7_counts[name]):
                page_number = (script.EXPECTED_PART7_PAGE_RANGE[0] +
                               index % (script.EXPECTED_PART7_PAGE_RANGE[1] -
                                       script.EXPECTED_PART7_PAGE_RANGE[0] + 1))
                value = row(
                    "part7", page_number=page_number, row_number=str(index + 1),
                    asset_name=f"{name}-{index}",
                    source_row_locator=(
                        f"p{page_number}-y{part7_locator_offset[name] + index + 1}"),
                    account_scope=f"investment-account-{index % 11 + 1}")
                if name == "transactions":
                    value["transaction_type"] = (
                        "purchase" if index <
                        script.EXPECTED_PART7_TRANSACTION_TYPES["purchase"]
                        else "sale")
                if name == "quarantined":
                    value["reasons"] = [
                        reason for reason, expected in
                        script.EXPECTED_PART7_QUARANTINE_REASONS.items()
                        if index < expected]
                rows.append(value)
            for index in range(count - len(rows)):
                value = row("part2", page_number=2, row_number=str(index + 1),
                            asset_name=f"{name}-other-{index}")
                if name == "quarantined":
                    value["reasons"] = ["fixture"]
                rows.append(value)
            values[name] = rows
        return {
            "source_url": script.TRUMP_2025_SOURCE_URL,
            "source_sha256": script.TRUMP_2025_SOURCE_SHA256,
            "parser_version": script.TRUMP_2025_PARSER_VERSION,
            "page_count": script.TRUMP_2025_PAGE_COUNT,
            "printed_row_count": 28099,
            "source_row_census_status": "ocr_detected_rows_only",
            "source_row_census_complete": False,
            "ocr_checkpoint": {
                "reused_shard_count": 38,
                "reused_parser_version": "whitehouse-278e-hybrid-geometry/v5",
                "created_shard_count": 0,
                "pending_page_count": 0,
                "completed_page_count": 927,
            },
            **values,
        }

    def test_fixed_replay_is_conserved_and_deduplicated(self):
        audit = script.audit_replay(
            self.fixture(), extraction_sha256=script.EXPECTED_EXTRACTION_SHA256,
            legacy_tree=script.LEGACY_TREE,
            legacy_audit_sha256=script.LEGACY_AUDIT_SHA256)
        self.assertEqual(audit["printed_row_count"], 28099)
        self.assertEqual(audit["part6_physical_row_count"], 6322)
        self.assertEqual(audit["part6_numeric_printed_holding_count"], 2075)
        self.assertEqual(audit["part6_synthetic_row_number_holding_count"], 1923)
        self.assertEqual(audit["part6_duplicate_source_row_locator_excess"], 0)
        self.assertEqual(audit["qualified_part6_holding_duplicate_count"], 0)
        self.assertEqual(audit["part7_disposition_counts"],
                         script.EXPECTED_PART7_COUNTS)
        self.assertEqual(audit["part7_transaction_type_counts"],
                         script.EXPECTED_PART7_TRANSACTION_TYPES)
        self.assertEqual(audit["part7_page_range"], [159, 845])

    def test_changed_count_or_duplicate_locator_fails_closed(self):
        value = self.fixture()
        value["quarantined"].pop()
        with self.assertRaisesRegex(ValueError, "disposition counts changed"):
            script.audit_replay(
                value, extraction_sha256=script.EXPECTED_EXTRACTION_SHA256,
                legacy_tree=script.LEGACY_TREE,
                legacy_audit_sha256=script.LEGACY_AUDIT_SHA256)
        value = self.fixture()
        value["holdings"][1]["source_row_locator"] = value["holdings"][0][
            "source_row_locator"]
        with self.assertRaisesRegex(ValueError, "physical row locator is duplicated"):
            script.audit_replay(
                value, extraction_sha256=script.EXPECTED_EXTRACTION_SHA256,
                legacy_tree=script.LEGACY_TREE,
                legacy_audit_sha256=script.LEGACY_AUDIT_SHA256)
        value = self.fixture()
        value["holdings"][0]["row_number"] = "ocr-p1-y1"
        with self.assertRaisesRegex(ValueError, "printed/synthetic holding census changed"):
            script.audit_replay(
                value, extraction_sha256=script.EXPECTED_EXTRACTION_SHA256,
                legacy_tree=script.LEGACY_TREE,
                legacy_audit_sha256=script.LEGACY_AUDIT_SHA256)

    def test_changed_extraction_bytes_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "fixed extraction bytes changed"):
            script.audit_replay(
                self.fixture(), extraction_sha256="a" * 64,
                legacy_tree=script.LEGACY_TREE,
                legacy_audit_sha256=script.LEGACY_AUDIT_SHA256)


if __name__ == "__main__":
    unittest.main()
