import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location(
    "verify_prepared_publication", ROOT / "scripts/verify_prepared_publication.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


class Delegate:
    def __init__(self):
        self.calls = []

    def get(self, url, limit, *, api=False):
        self.calls.append((url, limit, api))
        return b"raw"


class PreparedPublicationTests(unittest.TestCase):
    def test_fixed_transport_resolves_candidate_without_forwarding_api_credentials(self):
        delegate = Delegate()
        commit = "a" * 40
        transport = script.FixedCommitTransport(commit, delegate=delegate)
        resolved = json.loads(transport.get("https://api.example.invalid", 100, api=True))
        self.assertEqual(resolved, {"object": {"type": "commit", "sha": commit}})
        self.assertEqual(delegate.calls, [])
        self.assertEqual(transport.get("https://raw.example.invalid", 100), b"raw")
        self.assertEqual(delegate.calls, [("https://raw.example.invalid", 100, False)])

    def test_fixed_transport_rejects_non_commit(self):
        with self.assertRaisesRegex(ValueError, "main commit"):
            script.FixedCommitTransport("main")


if __name__ == "__main__":
    unittest.main()
