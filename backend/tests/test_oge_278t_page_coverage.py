"""Bounded OGE page sidecar has no path into formal qualification."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_oge_278t_page_coverage as sidecar


class Oge278TPageCoverageTests(unittest.TestCase):
    def test_only_fixed_source_in_current_batch_is_observed(self):
        content = b"%PDF-1.7\nfixed synthetic bytes"
        sha = hashlib.sha256(content).hexdigest()
        doc_id = sidecar.TRUMP_SEPT_2026_DOCUMENT_ID
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf_path = root / "oge" / "reports" / doc_id / f"{sha}.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(content)
            metadata = {"schema_version": "oge-278t-archive/v1", "source_id": "oge",
                        "document_id": doc_id, "sha256": sha,
                        "archive_path": f"oge/reports/{doc_id}/{sha}.pdf"}
            extraction_dir = root / "extractions"
            extraction_dir.mkdir()
            (extraction_dir / f"{doc_id}.json").write_text(json.dumps({
                "document_id": doc_id, "source_sha256": sha,
                "parser_version": "oge-278t-pdf/v6"}), encoding="utf-8")
            batch = {"schema_version": "oge-278t-archive-batch/v1",
                     "reports": [metadata, {"document_id": "other", "sha256": "b" * 64}]}

            class Document:
                pages = [SimpleNamespace() for _ in range(7)]
                pages[6] = SimpleNamespace(width=800, extract_words=lambda: [
                    {"x0": 70, "top": 120, "text": "166"}])

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

            coverage = {"physical_slot_count": 2, "slots": [
                {"status": "ocr_row_mapped"},
                {"status": "physical_slot_without_ocr_row"}]}
            observed = {"raster_ocr_coverage": coverage}
            with (patch.object(sidecar, "TRUMP_SEPT_2026_SOURCE_SHA256", sha),
                  patch.object(sidecar, "pdfplumber", SimpleNamespace(
                      open=lambda _: Document())),
                  patch.object(sidecar, "inspect_pdf", return_value=observed) as inspect):
                result = sidecar.build_sidecars(batch, root, extraction_dir,
                                                root / "out", root / "review")
            inspect.assert_called_once()
            self.assertEqual(inspect.call_args.kwargs["page_numbers"], [7])
            self.assertEqual(inspect.call_args.kwargs["raster_ocr_source_sha256"], sha)
            self.assertEqual(result["selected_count"], 1)
            self.assertEqual(result["sidecar_count"], 1)
            self.assertEqual(result["official_transaction_change"], 0)
            saved = json.loads((root / "out" / doc_id / f"{sha}.json").read_text())
            self.assertEqual(saved["physical_slot_count"], 2)
            self.assertEqual(saved["unmatched_slot_count"], 1)
            self.assertFalse(saved["production_mutation_authorized"])

    def test_fixed_source_failure_is_a_separate_sidecar(self):
        sha = "a" * 64
        doc_id = sidecar.TRUMP_SEPT_2026_DOCUMENT_ID
        batch = {"schema_version": "oge-278t-archive-batch/v1", "reports": [{
            "schema_version": "oge-278t-archive/v1", "source_id": "oge",
            "document_id": doc_id, "sha256": sha,
            "archive_path": f"oge/reports/{doc_id}/{sha}.pdf"}]}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(sidecar, "TRUMP_SEPT_2026_SOURCE_SHA256", sha):
            root = Path(folder)
            result = sidecar.build_sidecars(batch, root, root / "extractions",
                                            root / "out", root / "review")
            failures = list((root / "out" / doc_id).glob("*.failure-*.json"))
            self.assertEqual(result["failure_count"], 1)
            self.assertEqual(result["sidecar_count"], 0)
            self.assertEqual(len(failures), 1)
            self.assertFalse(json.loads(failures[0].read_text())[
                "production_mutation_authorized"])

    def test_existing_sidecar_skips_work_and_fixed_archive_backfills_once(self):
        sha = "a" * 64
        doc_id = sidecar.TRUMP_SEPT_2026_DOCUMENT_ID
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(sidecar, "TRUMP_SEPT_2026_SOURCE_SHA256", sha):
            root = Path(folder)
            review_sidecar = root / "review/oge/page_coverage" / doc_id / f"{sha}.json"
            review_sidecar.parent.mkdir(parents=True)
            review_sidecar.write_text(json.dumps({
                "schema_version": sidecar.SCHEMA, "source_sha256": sha,
                "selected_page_numbers": [7],
                "production_mutation_authorized": False}), encoding="utf-8")
            empty_batch = {"schema_version": "oge-278t-archive-batch/v1",
                           "reports": []}
            skipped = sidecar.build_sidecars(empty_batch, root / "archive",
                                             root / "extractions", root / "out",
                                             root / "review")
            self.assertEqual(skipped["skipped_existing_sidecar_count"], 1)
            self.assertEqual(skipped["sidecar_count"], 0)
            review_sidecar.unlink()
            metadata_path = root / "archive/oge/reports" / doc_id / f"{sha}.json"
            metadata_path.parent.mkdir(parents=True)
            metadata_path.write_text(json.dumps({
                "schema_version": "oge-278t-archive/v1", "source_id": "oge",
                "document_id": doc_id, "sha256": sha,
                "archive_path": f"oge/reports/{doc_id}/{sha}.pdf"}), encoding="utf-8")
            # The archived file was selected for a one-time backfill; no
            # network discovery or all-report loop is involved.
            result = sidecar.build_sidecars(empty_batch, root / "archive",
                                            root / "extractions", root / "out",
                                            root / "review")
            self.assertEqual(result["selected_count"], 1)
            self.assertEqual(result["backfilled_count"], 1)
            self.assertEqual(result["failure_count"], 1)


if __name__ == "__main__":
    unittest.main()
