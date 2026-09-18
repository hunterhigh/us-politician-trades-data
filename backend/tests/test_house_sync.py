from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import HouseFilingCandidate, HouseIndexError, archive_document
from unison_snapshot.house_sync import plan_checkpoint, record_result


def candidate(document_id: str, filed_date: str = "2026-09-17") -> HouseFilingCandidate:
    return HouseFilingCandidate(
        source_id="house_clerk", document_id=document_id, filing_type="P",
        filer_name=f"Hon. Example {document_id}", state_district="CA01", filing_year=2026,
        filed_date=filed_date,
        document_url=f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/{document_id}.pdf",
        verification_status="official_raw_unparsed",
    )


def discovery(*rows: HouseFilingCandidate) -> dict:
    return {
        "metadata": {
            "filing_year": 2026, "sha256": "a" * 64,
            "retrieved_at": "2026-09-18T00:00:00+00:00",
        },
        "filings": [asdict(row) for row in rows],
    }


class HouseSyncTests(unittest.TestCase):
    def test_pending_queue_prioritizes_latest_filing_date(self):
        older = candidate("20000001", filed_date="2026-01-03")
        newer = candidate("20000002", filed_date="2026-09-17")
        undated = candidate("20000003", filed_date="")
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = plan_checkpoint(discovery(older, newer, undated), Path(temporary),
                                         planned_at="2026-09-18T01:00:00Z")
            self.assertEqual(checkpoint["queue"], [newer.document_id, older.document_id,
                                                   undated.document_id])

    def test_plan_preserves_failures_and_recovers_archives_from_disk(self):
        first, second = candidate("20000001"), candidate("20000002")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = plan_checkpoint(discovery(first, second), root,
                planned_at="2026-09-18T01:00:00Z")
            self.assertEqual(checkpoint["counts"], {"pending": 2, "failed": 0, "archived": 0})
            record_result(checkpoint, first.document_id, "failed", error="temporary upstream failure",
                          result_at="2026-09-18T01:01:00Z")
            self.assertEqual(checkpoint["documents"][first.document_id]["attempts"], 1)
            archive_document(root, second, b"%PDF-1.7\nfixture\n%%EOF\n", {},
                             retrieved_at="2026-09-18T01:02:00Z")
            recovered = plan_checkpoint(discovery(first, second), root, checkpoint,
                planned_at="2026-09-18T01:03:00Z")
            self.assertEqual(recovered["documents"][first.document_id]["status"], "failed")
            self.assertEqual(recovered["documents"][second.document_id]["status"], "archived")
            self.assertEqual(recovered["queue"], [])
            retry = plan_checkpoint(discovery(first, second), root, recovered,
                planned_at="2026-09-18T01:16:00Z")
            self.assertEqual(retry["queue"], [first.document_id])

    def test_index_changes_and_disappearances_are_anomalies(self):
        first, second = candidate("20000001"), candidate("20000002")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_document(root, first, b"%PDF-1.7\nfixture\n%%EOF\n", {},
                             retrieved_at="2026-09-18T00:59:00Z")
            checkpoint = plan_checkpoint(discovery(first, second), root,
                planned_at="2026-09-18T01:00:00Z")
            changed = candidate(first.document_id, filed_date="2026-09-18")
            latest = plan_checkpoint(discovery(changed), root, checkpoint,
                planned_at="2026-09-18T02:00:00Z")
            kinds = {(item["document_id"], item["kind"]) for item in latest["anomalies"]}
            self.assertIn((first.document_id, "official_index_row_changed"), kinds)
            self.assertIn((first.document_id, "archive_metadata_mismatch"), kinds)
            self.assertIn((second.document_id, "missing_from_latest_index"), kinds)
            self.assertEqual(latest["documents"][first.document_id]["status"], "pending")

    def test_result_updates_require_bounded_evidence(self):
        first = candidate("20000001")
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = plan_checkpoint(discovery(first), Path(temporary),
                planned_at="2026-09-18T01:00:00Z")
            with self.assertRaises(HouseIndexError):
                record_result(checkpoint, first.document_id, "failed", error="")
            with self.assertRaises(HouseIndexError):
                record_result(checkpoint, first.document_id, "archived",
                              archive_metadata={"sha256": "b" * 64})
            result = record_result(checkpoint, first.document_id, "archived",
                archive_metadata={"sha256": "b" * 64, "source_id": "house_clerk",
                                  "document_id": first.document_id, "filing_year": 2026,
                                  "source_url": first.document_url, "filer_name": first.filer_name,
                                  "filed_date": first.filed_date},
                result_at="2026-09-18T01:05:00Z")
            self.assertEqual(result["counts"], {"pending": 0, "failed": 0, "archived": 1})
            self.assertEqual(result["queue"], [])


if __name__ == "__main__":
    unittest.main()
