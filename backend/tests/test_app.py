"""Exercise the public CLI process, including its real-data fail-closed boundary."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from native_pdf_fixture_bytes import blank_native_pdf

BACKEND = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_market_commands_keep_v3_relations_and_renderer_with_one_processing_pass(self):
        import unison_snapshot.__main__ as cli
        from test_oge_annual_approved_policy import complete_case
        from unison_snapshot.codec import digest, encode
        _, (candidate, _) = complete_case()  # Synthetic approved-policy contract.
        actual_load = cli.load
        for command in ("build-alpaca-market-validation", "build-twelve-data-supplement"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temp:
                folder=Path(temp); result=deepcopy(candidate); calls=[]
                source=folder/"input.json"; source.write_text(json.dumps(candidate), encoding="utf8")
                output, audit, processed, html = [folder/name for name in
                    ("candidate.json", "audit.json", "processed.json", "dashboard.html")]
                processor=actual_load("process_snapshot", version="v3")
                process=Mock(wraps=processor.build_snapshot)
                def load(name, version="v1"):
                    calls.append((name,version))
                    return SimpleNamespace(build_snapshot=process) if name=="process_snapshot" else actual_load(name,version=version)
                counts={name:0 for name in ("symbol_count","market_row_count","incremental_ticker_count",
                    "full_refresh_ticker_count","split_refresh_ticker_count","historical_recovered_count",
                    "missing_ticker_count","unsupported_ticker_count","unresolved_ticker_count",
                    "recovered_ticker_count","accepted_count","attempted_count","remaining_count","request_count")}
                counts.update(cache_status="not_requested",historical_rejected_symbols=[])
                args=["unison_snapshot",command,"--input",str(source),"--output",str(output),
                    "--audit-output",str(audit),"--processed-output",str(processed),"--html-output",str(html),
                    "--distribution-authorized","--checked-at","2026-06-02T00:00:00Z"]
                with patch.object(sys,"argv",args), patch.object(cli,"AlpacaMarketClient"), \
                        patch.object(cli,"TwelveDataClient"), patch.object(cli,"load",side_effect=load), \
                        patch.object(cli,"build_market_validation",return_value=SimpleNamespace(snapshot=result,audit=counts)), \
                        patch.object(cli,"supplement_twelve_data",return_value=(result,counts)), redirect_stdout(StringIO()):
                    cli.main()
                self.assertEqual(calls,[("process_snapshot","v3"),("render_dashboard","v3")])
                self.assertEqual(process.call_count,1)
                saved=json.loads(output.read_bytes())
                # Compare against the previous two-pass CLI algorithm with the
                # real v3 processor, not only a dispatch/version assertion.
                previous=deepcopy(candidate)
                previous["meta"].update(generated_at="2026-06-02T00:00:00Z",snapshot_id="prepublication-validation")
                processor.build_snapshot(previous)
                identity=deepcopy(previous);identity["meta"].pop("snapshot_id",None)
                previous["meta"]["snapshot_id"]=digest(encode(identity))
                self.assertEqual(saved,previous)
                self.assertEqual(json.loads(processed.read_bytes()),processor.build_snapshot(previous))
                for key in ("transactions","reported_holdings"):
                    self.assertEqual(saved[key],candidate[key])
                for key in ("holding_report_coverage","transaction_disclosure_relations"):
                    self.assertEqual(saved["meta"][key],candidate["meta"][key])
                    self.assertEqual(json.loads(processed.read_bytes())["meta"][key],candidate["meta"][key])
                self.assertIn("transaction_disclosure_relations",html.read_text(encoding="utf8"))

    def test_market_commands_validate_before_writing_any_outputs(self):
        import unison_snapshot.__main__ as cli

        for command in ("build-alpaca-market-validation", "build-twelve-data-supplement"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                invalid = json.loads((BACKEND / "examples/synthetic.json").read_text(encoding="utf-8"))
                invalid["transactions"][0]["person_id"] = "missing-person"
                output, audit = folder / "candidate.json", folder / "audit.json"
                args = ["unison_snapshot", command, "--input", str(BACKEND / "examples/synthetic.json"),
                        "--output", str(output), "--audit-output", str(audit),
                        "--distribution-authorized", "--checked-at", "2026-09-19T00:00:00Z"]
                with patch.object(sys, "argv", args), patch.object(cli, "AlpacaMarketClient"), \
                        patch.object(cli, "TwelveDataClient"), \
                        patch.object(cli, "build_market_validation", return_value=SimpleNamespace(snapshot=invalid, audit={})), \
                        patch.object(cli, "supplement_twelve_data", return_value=(invalid, {"accepted_count": 0})), \
                        redirect_stderr(StringIO()) as error, self.assertRaises(SystemExit) as stopped:
                    cli.main()
                self.assertEqual(stopped.exception.code, 2)
                self.assertIn("person", error.getvalue().lower())
                self.assertFalse(output.exists())
                self.assertFalse(audit.exists())

    def invoke(self, *args, environment=None):
        process_environment = dict(os.environ, PYTHONPATH=str(BACKEND / "src"))
        if environment:
            process_environment.update(environment)
        return subprocess.run([sys.executable, "-m", "unison_snapshot", *args],
                              env=process_environment,
                              capture_output=True, text=True)

    def test_help_documents_demo_only_publication(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("publish-demo", result.stdout)
        self.assertIn("prepare-publication", result.stdout)
        self.assertIn("fetch-public", result.stdout)
        self.assertIn("discover-house-index", result.stdout)
        self.assertIn("archive-house-ptr", result.stdout)
        self.assertIn("parse-house-ptr", result.stdout)
        self.assertNotIn("create-house-ptr-review", result.stdout)
        self.assertNotIn("promote-house-ptr-review", result.stdout)
        self.assertIn("qualify-house-ptr", result.stdout)
        self.assertIn("build-house-candidate", result.stdout)
        self.assertIn("build-senate-candidate", result.stdout)
        self.assertIn("build-disclosure-candidate", result.stdout)
        self.assertIn("build-alpaca-market-validation", result.stdout)
        self.assertIn("parse-senate-members", result.stdout)
        self.assertIn("discover-senate-members", result.stdout)
        self.assertIn("parse-senate-search-page", result.stdout)
        self.assertIn("senate-efd-gate", result.stdout)
        self.assertIn("discover-senate-efd", result.stdout)
        self.assertIn("match-senate-catalog", result.stdout)
        self.assertIn("archive-senate-report-entrypoints", result.stdout)
        self.assertIn("extract-senate-report-entrypoints", result.stdout)
        self.assertIn("plan-senate-amendment-backfill", result.stdout)
        self.assertIn("resolve-senate-amendment-backfill", result.stdout)
        self.assertIn("activate-senate-amendment-supplement", result.stdout)
        self.assertIn("archive-senate-paper-pages", result.stdout)
        self.assertIn("extract-senate-paper-pages", result.stdout)
        self.assertIn("oge-gate", result.stdout)
        self.assertIn("discover-oge", result.stdout)
        self.assertIn("archive-oge-direct-pdfs", result.stdout)
        self.assertIn("extract-oge-direct-pdfs", result.stdout)
        self.assertIn("build-oge-candidate", result.stdout)
        self.assertIn("discover-house-members", result.stdout)
        self.assertIn("suggest-house-identity", result.stdout)
        self.assertIn("plan-house-ptr-sync", result.stdout)
        self.assertNotIn("record-house-ptr-result", result.stdout)
        self.assertNotIn("publish-live", result.stdout)

    def test_oge_extract_cli_reports_a_verified_checkpoint_hit(self):
        document_id = "42300720a4227e9e85258e77002dd1b3"
        pdf = blank_native_pdf()
        sha = hashlib.sha256(pdf).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            archive = folder / "evidence"
            report = archive / "oge" / "reports" / document_id
            report.mkdir(parents=True)
            pdf_path = report / f"{sha}.pdf"
            pdf_path.write_bytes(pdf)
            metadata = {
                "schema_version": "oge-278t-archive/v1", "source_id": "oge",
                "document_id": document_id,
                "document_url": ("https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"
                                 f"{document_id}/$FILE/Example-278T.pdf"),
                "filer_name": "Example, Ada", "agency": "Example Agency",
                "position_title": "Director", "catalog_added_date": "2026-09-19",
                "sha256": sha, "byte_length": len(pdf),
                "archive_path": pdf_path.relative_to(archive).as_posix(),
            }
            (report / f"{sha}.json").write_text(json.dumps(metadata), encoding="utf-8")
            batch = folder / "batch.json"
            batch.write_text(json.dumps({"reports": [metadata]}), encoding="utf-8")
            cached = {
                "schema_version": "oge-278t-extraction/v1",
                "parser_version": "oge-278t-pdf/v2", "source_id": "oge",
                "document_id": document_id, "source_url": metadata["document_url"],
                "source_sha256": sha, "catalog_filer_name": "Example, Ada",
                "agency": "Example Agency", "position_title": "Director",
                "catalog_added_date": "2026-09-19", "filed_at": None,
                "document_reasons": ["filer_signature_date_not_unique"],
                "evidence_complete": False, "transactions": [], "quarantined": [],
            }
            reuse = folder / "review" / document_id / sha
            reuse.mkdir(parents=True)
            (reuse / "oge-278t-pdf-v2.json").write_text(
                json.dumps(cached), encoding="utf-8")
            summary = folder / "summary.json"
            output_dir = folder / "output"
            result = self.invoke(
                "extract-oge-direct-pdfs", "--batch", str(batch),
                "--archive", str(archive), "--reuse-root", str(folder / "review"),
                "--output-dir", str(output_dir), "--output", str(summary))
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual((value["extraction_count"], value["reused_extraction_count"],
                              value["parsed_extraction_count"], value["failure_count"]),
                             (1, 1, 0, 0))
            self.assertEqual(json.loads((output_dir / f"{document_id}.json").read_text(
                encoding="utf-8")), cached)

    def test_real_input_fails_before_creating_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "input.json"
            source.write_text(json.dumps({"meta": dict(is_demo=False)}), encoding="utf-8")
            result = self.invoke("publish-demo", "--input", str(source), "--store", str(folder / "repo.git"),
                                 "--generated-at", "2026-09-18T00:01:00Z")
            self.assertEqual(result.returncode, 2)
            self.assertIn("Production admission", result.stderr)
            self.assertFalse((folder / "repo.git").exists())

    def test_alpaca_validation_requires_environment_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            result = self.invoke(
                "build-alpaca-market-validation",
                "--input", str(BACKEND / "examples/synthetic.json"),
                "--output", str(folder / "candidate.json"),
                "--audit-output", str(folder / "audit.json"),
                environment={"ALPACA_API_KEY_ID": "", "ALPACA_API_SECRET_KEY": ""},
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY are required", result.stderr)
            self.assertFalse((folder / "candidate.json").exists())
            self.assertFalse((folder / "audit.json").exists())

    def test_senate_gate_cli_is_disabled_by_default_and_requires_exact_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "gate.json"
            result = self.invoke("senate-efd-gate", "--output", str(output), environment={
                "SENATE_EFD_COLLECTION_ENABLED": "",
                "SENATE_EFD_TERMS_ACKNOWLEDGED": "",
            })
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "disabled")

            result = self.invoke("senate-efd-gate", "--output", str(output), environment={
                "SENATE_EFD_COLLECTION_ENABLED": "true",
                "SENATE_EFD_TERMS_ACKNOWLEDGED": "false",
            })
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "blocked")

            result = self.invoke("senate-efd-gate", "--output", str(output), environment={
                "SENATE_EFD_COLLECTION_ENABLED": "TRUE",
                "SENATE_EFD_TERMS_ACKNOWLEDGED": "false",
            })
            self.assertEqual(result.returncode, 2)
            self.assertIn("exactly true or false", result.stderr)

    def test_oge_gate_cli_is_default_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "gate.json"
            result = self.invoke("oge-gate", "--output", str(output), environment={
                "OGE_COLLECTION_ENABLED": "", "OGE_TERMS_ACKNOWLEDGED": ""})
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "disabled")
            result = self.invoke("oge-gate", "--output", str(output), environment={
                "OGE_COLLECTION_ENABLED": "true", "OGE_TERMS_ACKNOWLEDGED": "false"})
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "blocked")
