import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "backend/src"))
from unison_snapshot.legacy import PROCESSOR_V2_SHA256

spec = importlib.util.spec_from_file_location("fetch_snapshot_cli", ROOT / "client/fetch_snapshot.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class FetchSnapshotCLITests(unittest.TestCase):
    def test_published_market_contract_and_legacy_demo_are_readable(self):
        for licensed in (False, True):
            with self.subTest(licensed=licensed), tempfile.TemporaryDirectory() as directory:
                snapshot = json.loads((ROOT / "backend/examples/synthetic.json").read_text(encoding="utf-8"))
                snapshot["meta"]["snapshot_id"] = "cli-contract-fixture"
                if licensed:
                    snapshot["meta"]["processor_sha256"] = PROCESSOR_V2_SHA256
                    snapshot["source_health"].append({
                        "source_id": "twelve_data_split_adjusted_eod",
                        "source": "Twelve Data split-adjusted EOD",
                        "status": "ok", "last_checked_at": "2026-09-18T00:01:00Z",
                    })
                output = Path(directory) / "snapshot.json"
                args = ["fetch_snapshot.py", "ticker", "--value", "ZZDEMO",
                        "--owner", "owner", "--repo", "repo", "--output", str(output)]
                with patch.object(sys, "argv", args), patch.object(cli, "PublicSnapshotRepository") as repository:
                    repository.return_value.fetch.return_value = SimpleNamespace(commit="1" * 40, snapshot=snapshot)
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(cli.main(), 0)
                result = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(result["meta"]["snapshot_commit"], "1" * 40)
                original = {row["id"]: row for row in snapshot["transactions"]}
                self.assertEqual({row["id"] for row in result["transactions"]}, set(original))
                for row in result["transactions"]:
                    self.assertEqual({key: row[key] for key in original[row["id"]]}, original[row["id"]])
                if licensed:
                    self.assertIn("twelve_data_split_adjusted_eod", [row["source_id"] for row in result["source_health"]])


if __name__ == "__main__":
    unittest.main()
