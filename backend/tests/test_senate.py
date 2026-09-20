from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.senate import (
    SenateEfdError,
    SenateEfdClient,
    SenateSourceConfig,
    build_discovery,
    collection_gate_status,
    discover_ptrs,
    normalize_transaction_type,
    parse_search_page,
    require_collection_enabled,
    source_config_from_environment,
)


PTR_ID = "12345678-1234-4234-8234-1234567890ab"
PAPER_ID = "87654321-4321-4321-8321-ba0987654321"


def row(kind: str = "ptr", document_id: str = PTR_ID) -> list[str]:
    return [
        "Ada",
        "Example",
        "United States Senator from California",
        (f'<a href="/search/view/{kind}/{document_id}/" target="_blank">'
         'Periodic Transaction Report for 09/19/2026</a>'),
        "09/19/2026",
    ]


def payload(rows: list[list[str]], total: int | None = None) -> dict:
    count = len(rows) if total is None else total
    return {"draw": 1, "recordsTotal": count, "recordsFiltered": count,
            "data": rows, "result": "ok"}


class SenateDiscoveryTests(unittest.TestCase):
    def test_source_gate_is_disabled_and_requires_both_explicit_flags(self):
        with self.assertRaisesRegex(SenateEfdError, "disabled"):
            require_collection_enabled()
        with self.assertRaisesRegex(SenateEfdError, "terms"):
            require_collection_enabled(SenateSourceConfig(enabled=True))
        require_collection_enabled(SenateSourceConfig(enabled=True, terms_acknowledged=True))

    def test_environment_gate_requires_exact_explicit_values(self):
        self.assertEqual(collection_gate_status(source_config_from_environment({}))["status"],
                         "disabled")
        blocked = source_config_from_environment({"SENATE_EFD_COLLECTION_ENABLED": "true"})
        self.assertEqual(collection_gate_status(blocked)["status"], "blocked")
        enabled = source_config_from_environment({
            "SENATE_EFD_COLLECTION_ENABLED": "true",
            "SENATE_EFD_TERMS_ACKNOWLEDGED": "true",
        })
        self.assertEqual(collection_gate_status(enabled)["status"], "enabled")
        with self.assertRaisesRegex(SenateEfdError, "exactly true or false"):
            source_config_from_environment({"SENATE_EFD_COLLECTION_ENABLED": "TRUE"})

    def test_discovery_checks_both_gates_before_session_or_network(self):
        class Client:
            begun = False

            def begin_authorized_session(self):
                self.begun = True

        client = Client()
        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(SenateEfdError, "disabled"):
            discover_ptrs(Path(temp), SenateSourceConfig(), submitted_start_date="2026-01-01",
                          client=client)
        self.assertFalse(client.begun)

        with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(SenateEfdError, "terms"):
            discover_ptrs(Path(temp), SenateSourceConfig(enabled=True),
                          submitted_start_date="2026-01-01", client=client)
        self.assertFalse(client.begun)

    def test_enabled_discovery_archives_raw_catalog_without_fetching_reports(self):
        raw = json.dumps(payload([row()]), separators=(",", ":")).encode("utf-8")

        class Client:
            begun = False
            calls = []

            def begin_authorized_session(self):
                self.begun = True

            def download_search_page(self, **kwargs):
                self.calls.append(kwargs)
                return raw, {"content-type": "application/json"}

        client = Client()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = discover_ptrs(
                root, SenateSourceConfig(enabled=True, terms_acknowledged=True),
                submitted_start_date="2026-01-01", client=client)
            self.assertTrue(client.begun)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(result["records_total"], 1)
            self.assertEqual(result["reports"][0]["access_method"], "electronic_ptr")
            self.assertNotIn("filed_at", result["reports"][0])
            metadata = result["metadata"]
            self.assertTrue((root / metadata["archive_path"]).is_file())
            self.assertTrue((root / metadata["pages"][0]["archive_path"]).is_file())

    def test_http_client_uses_the_agreement_post_before_catalog_search(self):
        class Response:
            def __init__(self, body, url, content_type="text/html"):
                self.body, self.url, self.status = body, url, 200
                self.headers = {"Content-Type": content_type}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return self.url

            def read(self, maximum):
                return self.body[:maximum]

        class Opener:
            requests = []

            def open(self, request, timeout):
                self.requests.append(request)
                if len(self.requests) == 1:
                    body = b'<input name="csrfmiddlewaretoken" value="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">'
                    return Response(body, "https://efdsearch.senate.gov/search/home/")
                if len(self.requests) == 2:
                    body = b'<input name="csrfmiddlewaretoken" value="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">'
                    return Response(body, "https://efdsearch.senate.gov/search/")
                return Response(json.dumps(payload([row()])).encode("utf-8"),
                                "https://efdsearch.senate.gov/search/report/data/",
                                "application/json")

        opener = Opener()
        client = SenateEfdClient(opener=opener)
        client.begin_authorized_session()
        content, _ = client.download_search_page(
            start=0, length=100, draw=1, submitted_start_date="01/01/2026 00:00:00")
        self.assertEqual(json.loads(content)["recordsTotal"], 1)
        self.assertEqual([request.get_method() for request in opener.requests], ["GET", "POST", "POST"])
        self.assertIn(b"prohibition_agreement=1", opener.requests[1].data)
        self.assertIn(b"report_types=%5B11%5D", opener.requests[2].data)

    def test_classifies_electronic_and_paper_ptr_without_inventing_filed_at(self):
        page = parse_search_page(payload([row(), row("paper", PAPER_ID)]), start=0, length=2)
        discovery = build_discovery([page])
        self.assertEqual([report["access_method"] for report in discovery["reports"]],
                         ["electronic_ptr", "paper_ptr"])
        self.assertEqual(discovery["reports"][0]["portal_listed_date"], "2026-09-19")
        self.assertNotIn("filed_at", discovery["reports"][0])

    def test_field_drift_fails_closed(self):
        cases = [payload([row() + ["new column"]]), payload([row()[:-1]])]
        response_extra = payload([row()])
        response_extra["next"] = "cursor"
        cases.append(response_extra)
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(SenateEfdError):
                parse_search_page(candidate, start=0, length=1)

    def test_non_official_and_disguised_urls_fail_closed(self):
        official = row()
        malicious = [
            official[3].replace("/search/view/", "https://evil.example/search/view/"),
            official[3].replace("/search/view/", "//evil.example/search/view/"),
            official[3].replace("/search/view/", "https://efdsearch.senate.gov@evil.example/search/view/"),
            official[3].replace("/\" target", "/?download=1\" target"),
            official[3].replace("/\" target", "/#fragment\" target"),
        ]
        for markup in malicious:
            bad = deepcopy(official)
            bad[3] = markup
            with self.subTest(markup=markup), self.assertRaises(SenateEfdError):
                parse_search_page(payload([bad]), start=0, length=1)

    def test_pagination_is_complete_and_order_independent(self):
        first = parse_search_page(payload([row()], total=2), start=0, length=1)
        second = parse_search_page(payload([row("paper", PAPER_ID)], total=2), start=1, length=1)
        self.assertEqual(build_discovery([first, second]), build_discovery([second, first]))
        with self.assertRaises(SenateEfdError):
            build_discovery([second])
        short = parse_search_page(payload([row()], total=2), start=0, length=2)
        with self.assertRaises(SenateEfdError):
            build_discovery([short])

    def test_exchange_is_preserved_and_quarantined(self):
        exchange = normalize_transaction_type("Exchange")
        self.assertEqual(exchange["transaction_type"], "exchange")
        self.assertEqual(exchange["qualification_status"], "quarantined")
        self.assertEqual(exchange["quarantine_reasons"], ["exchange_requires_contract_resolution"])
        self.assertEqual(normalize_transaction_type("Sale (Partial)")["transaction_type"], "sale")
        with self.assertRaises(SenateEfdError):
            normalize_transaction_type("Gift")


if __name__ == "__main__":
    unittest.main()
