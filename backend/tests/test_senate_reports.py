import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.senate import SenateEfdError, SenateSourceConfig
from unison_snapshot.senate_reports import (
    ELECTRONIC_EXTRACTION_SCHEMA,
    REPORT_ARCHIVE_SCHEMA,
    SenateReportClient,
    archive_catalog_report_entrypoints,
    archive_report,
    extract_archived_report_batch,
    inspect_paper_entrypoint,
    inspect_paper_ptr,
    inspect_report_content,
    parse_electronic_ptr,
)


DOCUMENT_ID = "b999bc0e-3eb0-4ca9-ab07-8e8f2e04b41f"


def discovery(method="electronic_ptr", document_id=DOCUMENT_ID):
    kind = "ptr" if method == "electronic_ptr" else "paper"
    return {
        "access_method": method,
        "catalog_index": 0,
        "document_id": document_id,
        "document_url": f"https://efdsearch.senate.gov/search/view/{kind}/{document_id}/",
        "filer_name": "Sample Senator",
        "office": "Sample, Senator (Senator)",
        "portal_listed_date": "2026-09-17",
        "report_amendment_number": None,
        "report_label_date": "2026-09-17",
        "report_type": "periodic_transaction_report",
        "source_id": "senate_efd",
    }


def html(rows=None, headers=None):
    headers = headers or [
        "#", "Transaction Date", "Owner", "Ticker", "Asset Name", "Asset Type",
        "Type", "Amount", "Comment",
    ]
    rows = rows if rows is not None else [[
        "1", "09/01/2026", "Spouse", "ACME", "Acme &amp; Co.", "Stock",
        "Purchase", "$1,001 - $15,000", "--",
    ], [
        "2", "09/02/2026", "", "--", "Municipal Fund", "Other",
        "Exchange", "$15,001 - $50,000", "See note",
    ]]
    head = "".join(f"<th>{cell}</th>" for cell in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    count = len(rows)
    unit = "transaction" if count == 1 else "transactions"
    return (f"<!doctype html><html><body>"
            f"<h1>Periodic Transaction Report for 09/17/2026</h1>"
            f"<h2>Sample Senator (Sample, Senator)</h2>"
            f"<p>Filed 09/17/2026 @ 8:55 AM</p>"
            f"<div>({count} {unit} total)</div>"
            f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
            f"</body></html>").encode()


def metadata_for(content, method="electronic_ptr", media_kind="html"):
    item = discovery(method)
    return {
        "schema_version": REPORT_ARCHIVE_SCHEMA,
        "source_id": "senate_efd",
        "document_id": item["document_id"],
        "document_url": item["document_url"],
        "access_method": method,
        "filer_name": item["filer_name"],
        "portal_listed_date": item["portal_listed_date"],
        "report_label_date": item["report_label_date"],
        "report_amendment_number": None,
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_length": len(content),
        "media_kind": media_kind,
    }


class FakeSession:
    def __init__(self, payload):
        self.csrf_token = None
        self.payload = payload
        self.calls = []

    def begin_authorized_session(self):
        self.csrf_token = "token"

    def _open(self, request, *, expected_urls, maximum):
        self.calls.append((request.full_url, expected_urls, maximum))
        return self.payload, {"content-type": "text/html; charset=utf-8"}


class SenateReportsTest(unittest.TestCase):
    def test_download_requires_both_source_gates(self):
        fake = FakeSession(html())
        for config in (SenateSourceConfig(), SenateSourceConfig(enabled=True)):
            with self.subTest(config=config), self.assertRaises(SenateEfdError):
                SenateReportClient(config, client=fake).download(discovery())
        self.assertEqual(fake.calls, [])
        content, headers = SenateReportClient(
            SenateSourceConfig(enabled=True, terms_acknowledged=True), client=fake,
        ).download(discovery())
        self.assertEqual(content, html())
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(fake.calls[0][0], discovery()["document_url"])

    def test_official_url_and_kind_are_bound_to_catalog_identity(self):
        candidates = []
        for url in (
            discovery()["document_url"].replace("efdsearch.senate.gov", "evil.example"),
            discovery()["document_url"] + "?download=1",
            discovery()["document_url"].replace("/ptr/", "/paper/"),
        ):
            value = discovery()
            value["document_url"] = url
            candidates.append(value)
        for candidate in candidates:
            with self.subTest(url=candidate["document_url"]), self.assertRaises(SenateEfdError):
                inspect_report_content(candidate, html(), {"content-type": "text/html"})

    def test_archive_is_content_addressed_and_replayable(self):
        content = html()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = archive_report(
                root, discovery(), content, {"Content-Type": "text/html; charset=utf-8"},
                "2026-09-20T00:00:00+00:00",
            )
            self.assertEqual(result["schema_version"], REPORT_ARCHIVE_SCHEMA)
            self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())
            archived = root / result["archive_path"]
            self.assertEqual(archived.read_bytes(), content)
            saved = json.loads(archived.with_name(archived.stem + ".metadata.json").read_text())
            self.assertEqual(saved["document_id"], DOCUMENT_ID)
            self.assertEqual(saved["artifact_role"], "entrypoint_response")
            self.assertFalse(saved["evidence_complete"])
            repeated = archive_report(
                root, discovery(), content, {"Content-Type": "text/html; charset=utf-8"},
                "2026-09-21T00:00:00+00:00",
            )
            self.assertEqual(repeated["archive_path"], result["archive_path"])

    def test_electronic_parser_preserves_raw_fields_and_quarantines_exchange(self):
        content = html()
        result = parse_electronic_ptr(metadata_for(content), content)
        self.assertEqual(result["schema_version"], ELECTRONIC_EXTRACTION_SCHEMA)
        self.assertEqual(result["document_disposition"], "transactions_parsed")
        self.assertTrue(result["evidence_complete"])
        self.assertEqual(result["filed_at_raw"], "Filed 09/17/2026 @ 8:55 AM")
        self.assertEqual(len(result["transactions"]), 2)
        first, second = result["transactions"]
        self.assertEqual(first["transaction_date"], "2026-09-01")
        self.assertEqual(first["ticker_raw"], "ACME")
        self.assertEqual(first["asset_name_raw"], "Acme & Co.")
        self.assertEqual(first["transaction_type"], "purchase")
        self.assertEqual(first["amount_raw"], "$1,001 - $15,000")
        self.assertIsNone(second["owner_raw"])
        self.assertIsNone(second["ticker_raw"])
        self.assertEqual(second["transaction_type"], "exchange")
        self.assertEqual(second["qualification_status"], "quarantined")

    def test_amendment_and_catalog_title_date_difference_are_preserved(self):
        content = html(rows=[[
            "1", "09/01/2026", "Self", "ACME", "Acme", "Stock", "Purchase",
            "$1,001 - $15,000", "",
        ]]).replace(
            b"Periodic Transaction Report for 09/17/2026",
            b"Periodic Transaction Report for 09/17/2026 (Amendment 1)",
        )
        metadata = metadata_for(content)
        metadata["report_label_date"] = "2026-09-18"
        metadata["report_amendment_number"] = 1
        result = parse_electronic_ptr(metadata, content)
        self.assertEqual(result["report_title_date"], "2026-09-17")
        self.assertEqual(result["report_label_date"], "2026-09-18")
        self.assertEqual(result["report_amendment_number"], 1)
        self.assertFalse(result["catalog_title_date_matches"])

    def test_unnumbered_official_amendment_remains_explicit(self):
        content = html(rows=[[
            "1", "09/01/2026", "Self", "ACME", "Acme", "Stock", "Purchase",
            "$1,001 - $15,000", "",
        ]]).replace(
            b"Periodic Transaction Report for 09/17/2026",
            b"Periodic Transaction Report for 09/17/2026 (Amendment)",
        )
        metadata = metadata_for(content)
        metadata["report_amendment_number"] = "unspecified"
        result = parse_electronic_ptr(metadata, content)
        self.assertEqual(result["report_amendment_number"], "unspecified")

    def test_parser_fails_closed_on_table_or_row_drift(self):
        bad_header = html(headers=["#", "Date"])
        bad_width = html(rows=[["1", "09/01/2026"]])
        bad_date = html(rows=[[
            "1", "2026-09-01", "Self", "ACME", "Acme", "Stock", "Purchase",
            "$1,001 - $15,000", "",
        ]])
        for content in (bad_header, bad_width, bad_date):
            with self.subTest(content=content), self.assertRaises(SenateEfdError):
                parse_electronic_ptr(metadata_for(content), content)

    def test_empty_electronic_table_requires_review(self):
        content = html(rows=[])
        result = parse_electronic_ptr(metadata_for(content), content)
        self.assertEqual(result["document_disposition"], "empty_table_requires_review")
        self.assertEqual(result["transactions"], [])

    def test_paper_pdf_is_verified_but_not_guessed(self):
        content = b"%PDF-1.7\nexample\n%%EOF\n"
        headers = {"content-type": "application/pdf"}
        inspected = inspect_report_content(discovery("paper_ptr"), content, headers)
        self.assertEqual(inspected["media_kind"], "pdf")
        result = inspect_paper_ptr(metadata_for(content, "paper_ptr", "pdf"), content)
        self.assertEqual(result["document_disposition"], "paper_pdf_requires_bounded_parser")
        self.assertFalse(result["evidence_complete"])
        self.assertEqual(result["transactions"], [])

    def test_paper_html_is_only_an_unresolved_viewer_entrypoint(self):
        content = html(rows=[])
        inspected = inspect_report_content(
            discovery("paper_ptr"), content, {"content-type": "text/html"})
        self.assertEqual(inspected["media_kind"], "html")
        metadata = metadata_for(content, "paper_ptr", "html")
        result = inspect_paper_entrypoint(metadata, content)
        self.assertEqual(result["document_disposition"],
                         "paper_viewer_requires_page_collection")
        self.assertFalse(result["evidence_complete"])

    def test_bounded_batch_reuses_session_and_resumes_archived_documents(self):
        second_id = "a1111111-1111-4111-8111-111111111111"
        reports = [discovery(), discovery(document_id=second_id)]
        reports[0]["catalog_index"] = 1
        reports[1]["catalog_index"] = 2
        batch = {
            "schema_version": "senate-efd-discovery/v1",
            "metadata": {"sha256": "a" * 64},
            "reports": reports,
        }
        fake = FakeSession(html())
        config = SenateSourceConfig(enabled=True, terms_acknowledged=True)
        with tempfile.TemporaryDirectory() as temporary:
            first = archive_catalog_report_entrypoints(
                Path(temporary), batch, limit=1, config=config, client=fake)
            second = archive_catalog_report_entrypoints(
                Path(temporary), batch, limit=2, config=config, client=fake)
        self.assertEqual((first["archived_count"], first["archived_total"], first["pending_count"]),
                         (1, 1, 1))
        self.assertEqual((second["archived_count"], second["archived_total"], second["pending_count"]),
                         (1, 2, 0))
        self.assertEqual(len(fake.calls), 2)

    def test_archived_batch_extracts_electronic_entrypoint_offline(self):
        batch = {
            "schema_version": "senate-efd-discovery/v1",
            "metadata": {"sha256": "a" * 64},
            "reports": [discovery()],
        }
        config = SenateSourceConfig(enabled=True, terms_acknowledged=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collected = archive_catalog_report_entrypoints(
                root, batch, limit=1, config=config, client=FakeSession(html()))
            extracted = extract_archived_report_batch(root, collected)
        self.assertEqual((extracted["extraction_count"], extracted["failure_count"],
                          extracted["transaction_count"]), (1, 0, 2))
        self.assertTrue(extracted["extractions"][0]["evidence_complete"])

    def test_archive_timestamp_requires_timezone(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(SenateEfdError):
            archive_report(Path(temporary), discovery(), html(),
                           {"content-type": "text/html"}, "2026-09-20T12:00:00")


if __name__ == "__main__":
    unittest.main()
