"""The annual entry keeps structured parsing explicit and repeatable."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def entry():
    path = Path(__file__).resolve().parents[1] / "src/unison_snapshot/commands/senate_annual.py"
    spec = importlib.util.spec_from_file_location("annual_cli_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SenateAnnualCliTests(unittest.TestCase):
    def test_explicit_structured_flag_reaches_existing_review_builder(self):
        module = entry()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roster = root / "roster.json"
            roster.write_text("{}", encoding="utf-8")
            args = ["build", "--evidence-root", str(root / "e"), "--review-root", str(root / "r"),
                    "--roster", str(roster)]
            result = {"selected_report_count": 1, "qualified_report_count": 1, "holding_count": 2}
            for enabled in (False, True):
                with self.subTest(enabled=enabled), redirect_stdout(io.StringIO()), patch.object(
                        module, "build_annual_review", return_value=result) as build:
                    self.assertEqual(module.main(args + (["--structured-asset-labels"] if enabled else [])), 0)
                self.assertEqual(build.call_args.kwargs, {"structured_asset_labels": True} if enabled else {})

    def test_workflow_reuses_activated_mode_without_unrequested_collection(self):
        text = (Path(__file__).resolve().parents[2] / ".github/workflows/senate-annual.yml").read_text(encoding="utf-8")
        for name in ("Discover and archive latest annual versions", "Publish official originals and catalog"):
            step = text.split("- name: " + name, 1)[1].split("- name:", 1)[0]
            self.assertIn("if: inputs.structured_asset_labels != true", step)
        self.assertIn('"$active_mode" = "structured_v4"', text)
        self.assertIn('annual_args+=(--structured-asset-labels)', text)
        self.assertIn('"${annual_args[@]}"', text)


if __name__ == "__main__":
    unittest.main()
