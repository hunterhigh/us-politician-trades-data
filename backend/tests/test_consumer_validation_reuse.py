import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_public_repo import production_payload
from unison_snapshot import __main__ as cli, builder
from unison_snapshot.codec import digest, encode
from unison_snapshot.legacy import PROCESSOR_V2_SHA256, PROCESSOR_V3_SHA256


class ConsumerValidationReuseTests(unittest.TestCase):
    def test_normalization_cannot_turn_a_valid_positive_close_into_published_zero(self):
        from test_market_store import row
        from unison_snapshot.market_store import validate_market_rows
        value = row()
        value["price_history"][0]["close"] = 0.00001
        with self.assertRaisesRegex(ValueError, "rounds to zero"):
            validate_market_rows([value], data_cutoff_at="2026-09-21T00:00:00Z")

    def run_prepare(self, *, change_input=False, change_processor=False, invalid_structure=False, version="v2"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = production_payload()
            if version == "v3":
                data["meta"]["amount_policy"] = "bounded-and-open-separate/v1"
            if invalid_structure:
                data["people"].append(dict(data["people"][0]))
            raw = encode(data)
            (root / "candidate.json").write_bytes(raw + (b" " if change_input else b""))
            (root / "receipt.json").write_bytes(encode({"input_sha256": digest(raw),
                "processor_sha256": (PROCESSOR_V2_SHA256 if version == "v3" else PROCESSOR_V3_SHA256)
                    if change_processor else PROCESSOR_V3_SHA256 if version == "v3" else PROCESSOR_V2_SHA256}))
            args = ["unison_snapshot", "prepare-publication", "--input", str(root / "candidate.json"),
                    "--root", str(root / "published"), "--generated-at", "2026-09-21T00:00:00Z",
                    "--consumer-validation-file", str(root / "receipt.json")]
            with patch.object(sys, "argv", args), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()), patch.object(
                        builder, "load", side_effect=AssertionError("Already rendered; no duplicate processing")):
                cli.main()
            return json.loads((root / "published/manifest.json").read_bytes())

    def test_exact_render_receipt_reuses_processing_and_still_builds_publication(self):
        manifest = self.run_prepare()
        self.assertEqual(manifest["processor_sha256"], PROCESSOR_V2_SHA256)
        self.assertFalse(manifest["is_demo"])

    def test_changed_bytes_or_processor_cannot_reuse_rendering(self):
        for version in ("v2", "v3"):
            for option in ("change_input", "change_processor"):
                with self.subTest(option=option, version=version), self.assertRaises(SystemExit) as error:
                    self.run_prepare(version=version, **{option: True})
                self.assertEqual(error.exception.code, 2)

    def test_v3_exact_render_receipt_uses_same_version_in_manifest(self):
        self.assertEqual(self.run_prepare(version="v3")["processor_sha256"], PROCESSOR_V3_SHA256)

    def test_reuse_keeps_structural_publication_validation(self):
        with self.assertRaises(SystemExit) as error:
            self.run_prepare(invalid_structure=True)
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
