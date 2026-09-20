import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge import OgeCatalogError, OgeSourceConfig
from unison_snapshot.oge_reports import (
    EXTRACTION_SCHEMA, PARSER_VERSION, OgePdfClient, archive_direct_batch, archive_direct_pdf,
    parse_archived_pdf, parse_table_rows,
)


DOCUMENT_ID = "42300720a4227e9e85258e77002dd1b3"
URL = ("https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"
       f"{DOCUMENT_ID}/$FILE/Example-278T.pdf")
PDF = b"%PDF-1.7\nminimal test envelope\n%%EOF\n"


def record(access_method="direct_pdf"):
    return {
        "catalog_index": 0, "catalog_added_date": "2026-09-19",
        "document_type": "278_transaction", "filer_name": "Example, Ada",
        "agency": "Example Agency", "position_title": "Director", "level": "n/a",
        "amended_label": None, "pending_final_oge_disposition": False,
        "access_method": access_method,
        "source_document_id": DOCUMENT_ID if access_method == "direct_pdf" else None,
        "document_url": URL if access_method == "direct_pdf" else
        "https://extapps2.oge.gov/201/Presiden.nsf/201%20Request?OpenForm&Filer=Example",
        "source_id": "oge",
    }


class Client:
    def download(self, url):
        return PDF, {"content-type": "application/pdf", "etag": "example"}


class OgeReportTests(unittest.TestCase):
    def test_pdf_client_requires_exact_direct_pdf_response(self):
        class Response:
            status = 200
            headers = {"Content-Type": "application/pdf", "ETag": "example"}

            def __init__(self, request):
                self.request = request

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return self.request.full_url

            def read(self, _):
                return PDF

        class Opener:
            request = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response(request)

        opener = Opener()
        content, headers = OgePdfClient(timeout=9, opener=opener).download(URL)
        self.assertEqual((content, headers["etag"]), (PDF, "example"))
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertEqual(opener.request.full_url, URL)

    def test_archives_only_direct_pdfs_immutably(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = archive_direct_pdf(root, record(), OgeSourceConfig(True, True),
                                          client=Client(), retrieved_at="2026-09-20T00:00:00Z")
            self.assertEqual(metadata["sha256"], hashlib.sha256(PDF).hexdigest())
            self.assertEqual((root / metadata["archive_path"]).read_bytes(), PDF)
            again = archive_direct_pdf(root, record(), OgeSourceConfig(True, True),
                                       client=Client(), retrieved_at="2026-09-21T00:00:00Z")
            self.assertEqual(again["retrieved_at"], "2026-09-20T00:00:00Z")
            with self.assertRaises(OgeCatalogError):
                archive_direct_pdf(root, record("request_required"),
                                   OgeSourceConfig(True, True), client=Client())

    def test_batch_counts_request_required_without_automating_it(self):
        with tempfile.TemporaryDirectory() as folder:
            result = archive_direct_batch(
                Path(folder), {"transactions": [record(), record("request_required")]},
                OgeSourceConfig(True, True), limit=1, client=Client())
            self.assertEqual((result["archived_count"], result["request_required_count"]), (1, 1))
            self.assertEqual(result["pending_count"], 0)

    def test_table_parser_promotes_supported_rows_and_quarantines_exchange(self):
        sha = "a" * 64
        rows = [
            (1, ["#", "DESCRIPTION", "TYPE", "DATE", "NOTIFICATION RECEIVED OVER 30 DAYS AGO", "AMOUNT"]),
            (1, ["1", "Coinbase Global, Inc. (COIN)", "Purchase", "05/01/2025", "", "$1,001 - $15,000"]),
            (1, ["2", "Spouse Investment Account #1", "", "", "No", ""]),
            (1, ["3", "Tesla, Inc. (TSLA)", "Sale", "05/02/2025", "Yes", "$1,000,001 - $5,000,000"]),
            (1, ["4", "Retirement Account #1", "", "", "No", ""]),
            (2, ["5", "Example Asset", "Exchange", "05/03/2025", "", "$50,001 - $100,000"]),
            (2, ["6", "Line is intentionally left blank", "", "", "No", ""]),
        ]
        transactions, quarantined = parse_table_rows(rows, source_sha=sha)
        self.assertEqual(len(transactions), 2)
        self.assertEqual((transactions[0]["ticker"], transactions[0]["owner"]), ("COIN", "Self"))
        self.assertEqual((transactions[1]["ticker"], transactions[1]["owner"]), ("TSLA", "Spouse"))
        self.assertEqual((transactions[1]["amount_low"], transactions[1]["amount_high"]),
                         (1000001, 5000000))
        self.assertEqual(quarantined[0]["reasons"], ["exchange_requires_contract_resolution"])

    def test_archived_parser_binds_hash_signature_and_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = archive_direct_pdf(root, record(), OgeSourceConfig(True, True),
                                          client=Client(), retrieved_at="2026-09-20T00:00:00Z")
            metadata_path = root / "metadata.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            extracted = ("Electronic Signature - I certify. /s/ Example, Ada "
                         "[electronically signed on 06/03/2025 by Example, Ada in Integrity.gov] "
                         "Agency Ethics Official's Opinion /s/ Official "
                         "[electronically signed on 06/12/2025 by Official in Integrity.gov]",
                         [(1, ["1", "Example Inc. (EXM)", "Purchase", "05/01/2025", "", "$1,001 - $15,000"])])
            with patch("unison_snapshot.oge_reports._extract_pdf", return_value=extracted):
                result = parse_archived_pdf(root, metadata_path)
            self.assertEqual(result["schema_version"], EXTRACTION_SCHEMA)
            self.assertEqual(result["parser_version"], PARSER_VERSION)
            self.assertEqual(result["filed_at"], "2025-06-03")
            self.assertTrue(result["evidence_complete"])
            self.assertEqual(len(result["transactions"]), 1)


if __name__ == "__main__":
    unittest.main()
