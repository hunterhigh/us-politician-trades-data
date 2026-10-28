from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from unison_snapshot.commands.observe_oge_annual_part7 import (EXTRACTION_REL, PDF_REL, SIDECAR_REL,
                                      SOURCE_SHA, STATUS_REL, _hash_file,
                                      observe_fixed_report)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ObserveOgeAnnualPart7Tests(unittest.TestCase):
    def _roots(self, base: Path) -> tuple[Path, Path]:
        evidence, review = base / "evidence", base / "review"
        pdf = evidence / PDF_REL
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"test stand-in for pinned official PDF")
        extracted = review / EXTRACTION_REL
        extracted.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "oge_annual_vance_2026.json", extracted)
        return evidence, review

    def test_exact_source_and_extraction_attach_read_only_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self._roots(Path(folder))

            def pinned_pdf_hash(path: Path) -> str:
                return SOURCE_SHA if path.suffix == ".pdf" else _hash_file(path)

            with patch("unison_snapshot.commands.observe_oge_annual_part7._hash_file", side_effect=pinned_pdf_hash):
                status = observe_fixed_report(evidence, review)
            self.assertEqual(status["status"], "observed_shadow_only")
            ledger = json.loads((review / SIDECAR_REL).read_text(encoding="utf-8"))
            self.assertEqual((ledger["physical_slot_count"],
                              ledger["filled_transaction_count"],
                              ledger["blank_template_count"],
                              ledger["production_eligible_transaction_count"]),
                             (20, 10, 10, 0))
            self.assertFalse((review / "oge/annual/candidates").exists())
            self.assertEqual(json.loads((review / STATUS_REL).read_text())["status"],
                             "observed_shadow_only")

    def test_missing_or_changed_source_or_extraction_records_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            evidence, review = self._roots(Path(folder))
            status = observe_fixed_report(evidence, review)
            self.assertEqual(status["reason"], "archived_source_pdf_sha_mismatch")
            self.assertFalse((review / SIDECAR_REL).exists())
            with patch("unison_snapshot.commands.observe_oge_annual_part7._hash_file", side_effect=lambda path:
                       SOURCE_SHA if path.suffix == ".pdf" else _hash_file(path)):
                (review / EXTRACTION_REL).write_text("{}", encoding="utf-8")
                status = observe_fixed_report(evidence, review)
            self.assertEqual(status["reason"], "annual_v1_extraction_sha_mismatch")
            self.assertEqual(json.loads((review / STATUS_REL).read_text())["status"],
                             "coverage_unknown")
            self.assertFalse((review / SIDECAR_REL).exists())


if __name__ == "__main__":
    unittest.main()
