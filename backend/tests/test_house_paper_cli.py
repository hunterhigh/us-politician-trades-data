"""Saved paper interpretation is reachable without enabling new OCR."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_house_paper_pipeline as fixtures
from unison_snapshot.__main__ import main


class HousePaperCliTests(unittest.TestCase):
    def test_saved_flag_reaches_real_batch_and_writes_qualification_without_ocr(self):
        fixture = fixtures.HousePaperPipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.temporary.cleanup)
        fixture._write(fixture.review / "house_clerk/paper_observations" / fixture.relative,
                       fixture.observation)
        fixture._write(fixture.review / "house_clerk/paper_amount_headers" / fixture.relative,
                       fixture.header)
        batch = Path(fixture.temporary.name) / "batch.txt"
        batch.write_text("", encoding="utf-8")
        args = ["snapshot", "bridge-house-paper-batch", "--evidence-root", str(fixture.evidence),
                "--review-root", str(fixture.review), "--parse-batch", str(batch),
                "--tesseract", "missing-ocr-must-never-run", "--materialize-saved"]
        with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()) as output, patch(
                "unison_snapshot.house_paper_pipeline.qualify_paper_observation",
                return_value=fixture.paper), patch(
                "unison_snapshot.house_paper_pipeline.materialize_paper_observation",
                return_value=fixture.paper) as materialize, patch(
                "unison_snapshot.house_paper_pipeline.observe_house_ptr_pdf") as observe, patch(
                "unison_snapshot.house_paper_pipeline.observe_amount_header") as header, patch(
                "unison_snapshot.house_paper_pipeline.reread_doubtful_key_cells") as reread:
            main()
        self.assertEqual(json.loads(output.getvalue()), {"materialized_existing_observation": 1})
        materialize.assert_called_once()
        self.assertEqual(json.loads(fixture.qualification_path.read_text()), fixture.paper)
        observe.assert_not_called()
        header.assert_not_called()
        reread.assert_not_called()

    def test_default_command_keeps_existing_batch_call(self):
        args = ["snapshot", "bridge-house-paper-batch", "--evidence-root", "e", "--review-root", "r",
                "--parse-batch", "batch.txt", "--tesseract", "tesseract"]
        with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()), patch(
                "unison_snapshot.__main__.bridge_paper_batch", return_value={}) as bridge:
            main()
        self.assertNotIn("materialize_saved", bridge.call_args.kwargs)

    def test_controlled_workflow_opt_in_skips_parser_and_ocr(self):
        path = Path(__file__).resolve().parents[2] / ".github/workflows/house-review.yml"
        text = path.read_text(encoding="utf-8")
        self.assertIn("materialize_saved_paper:", text)
        self.assertIn("paper_args+=(--materialize-saved)", text)
        for name in ("Detect image-only PDFs in this batch", "Parse and prepare machine extraction artifacts",
                     "Refresh deterministic House identities", "Build automatic qualification artifacts"):
            step = text.split("- name: " + name, 1)[1].split("- name:", 1)[0]
            self.assertIn("if: inputs.materialize_saved_paper != true", step)
        self.assertIn(': > .local/parse-batch.txt', text)
        qualification_step = text.split("- name: Build automatic qualification artifacts", 1)[1].split("- name:", 1)[0]
        self.assertIn('[ "$method" != "source_bound_paper_cells" ] || continue', qualification_step)
        self.assertLess(qualification_step.index('source_bound_paper_cells'),
                        qualification_step.index('qualify-house-ptr'))


if __name__ == "__main__":
    unittest.main()
