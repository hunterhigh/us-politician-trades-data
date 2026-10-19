"""The existing House command persists actual annual relationship decisions."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_annual_transaction_candidate_relations import house_fixture, house_input
from test_house_candidate import BASE
from unison_snapshot.__main__ import main


class HouseAnnualRelationCliTests(unittest.TestCase):
    def test_existing_candidate_command_saves_bound_decisions_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, state = house_fixture(root)
            payload = house_input()
            source = payload["extraction"]["source"]
            relative = f"2025/{source['document_id']}/{payload['extraction']['source_sha256']}.json"
            for kind, value in (("holding_section_observations", payload["observation"]),
                                ("holding_extractions", payload["extraction"]),
                                ("holding_identities", payload["identity"])):
                path = root / "house_clerk" / kind / relative
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (root / "base.json").write_text(json.dumps(deepcopy(BASE)), encoding="utf-8")
            args = ["snapshot", "build-house-candidate", "--review-root", str(root),
                    "--state-status", str(root / "state.json"), "--base", str(root / "base.json"),
                    "--output", str(root / "candidate.json"),
                    "--annual-transaction-audit-out", str(root / "audit.json")]
            with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
                main()
            candidate = json.loads((root / "candidate.json").read_bytes())
            audit = json.loads((root / "audit.json").read_bytes())
            self.assertEqual(candidate["transactions"], [old])
            self.assertEqual(audit["observed_row_count"], 1)
            decision = audit["reports"][0]["rows"][0]
            self.assertEqual(decision["candidate_disposition"], "blocked_pending_relationship")
            self.assertIn("annual_saved_page_reproduction_unavailable", decision["candidate_blocking_reasons"])
            # The separate audit must never overwrite the candidate or HTML.
            args[-1] = str(root / "candidate.json")
            with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main()
