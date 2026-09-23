from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge import (
    OgeCatalogClient, OgeCatalogError, OgeSourceConfig, build_catalog, collection_gate_status,
    discover_catalog, parse_catalog_page, require_collection_enabled,
    source_config_from_environment,
)


DIRECT = (
    "<a href='https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"
    "42300720A4227E9E85258E77002DD1B3/$FILE/Example-278T.pdf'>278 Transaction</a>"
)
REQUEST = (
    "278 Transaction (<a href='https://extapps2.oge.gov/201/Presiden.nsf/"
    "201%20Request?OpenForm&Filer=Example'>Request this Document</a>)"
)


def row(type_markup: str = REQUEST) -> dict:
    return {
        "type": type_markup,
        "name": "Example, Ada",
        "agency": "Example Agency",
        "title": "Director",
        "level": "n/a",
        "docDate": "2026-09-19T04:21:52",
        "amended": "",
    }


def payload(rows: list[dict], total: int | None = None) -> dict:
    count = len(rows) if total is None else total
    return {"draw": 1, "recordsTotal": count, "recordsFiltered": count, "data": rows}


class OgeCatalogTests(unittest.TestCase):
    def test_classifies_direct_and_request_without_inventing_filing_date(self):
        annual = row("Annual (2026) (<a href='https://extapps2.oge.gov/201/Presiden.nsf/"
                     "201%20Request?OpenForm&Filer=Example'>Request this Document</a>)")
        page = parse_catalog_page(payload([row(DIRECT), row(REQUEST), annual]), start=0, length=3)
        catalog = build_catalog([page])
        self.assertEqual([item["access_method"] for item in catalog["transactions"]],
                         ["direct_pdf", "request_required"])
        self.assertEqual(catalog["transactions"][0]["catalog_added_date"], "2026-09-19")
        self.assertNotIn("filed_at", catalog["transactions"][0])
        self.assertNotIn("filing_id", catalog["transactions"][0])
        self.assertEqual(catalog["transactions"][0]["source_document_id"],
                         "42300720a4227e9e85258e77002dd1b3")
        self.assertIsNone(catalog["transactions"][1]["source_document_id"])

    def test_unrelated_legacy_markup_does_not_block_278_transactions(self):
        malformed_other = row(
            "Certificate of Divestiture OGE-2026-101 "
            "(<a href='https://extapps2.oge.gov/201/Presiden.nsf/"
            "201%20Request?OpenForm&Filer=Dell'Olio'>Request this Document</a>)")
        page = parse_catalog_page(
            payload([malformed_other, row(DIRECT)], total=2), start=0, length=2)
        self.assertEqual(len(page.transactions), 1)
        self.assertEqual(page.transactions[0].access_method, "direct_pdf")

    def test_duplicate_request_rows_are_preserved_as_catalog_occurrences(self):
        duplicate = row(REQUEST)
        catalog = build_catalog([
            parse_catalog_page(payload([duplicate, deepcopy(duplicate)]), start=0, length=2)
        ])
        self.assertEqual(len(catalog["transactions"]), 2)
        self.assertEqual([item["catalog_index"] for item in catalog["transactions"]], [0, 1])
        visible = [{key: value for key, value in item.items() if key != "catalog_index"}
                   for item in catalog["transactions"]]
        self.assertEqual(visible[0], visible[1])

    def test_field_drift_fails_closed(self):
        cases = []
        missing = row()
        missing.pop("amended")
        cases.append(payload([missing]))
        extra = row()
        extra["newField"] = "unexpected"
        cases.append(payload([extra]))
        response_extra = payload([row()])
        response_extra["next"] = "cursor"
        cases.append(response_extra)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(OgeCatalogError):
                parse_catalog_page(value, start=0, length=1)

    def test_non_official_and_disguised_urls_fail_closed(self):
        malicious = [
            REQUEST.replace("extapps2.oge.gov", "evil.example"),
            REQUEST.replace("https://", "http://"),
            REQUEST.replace("extapps2.oge.gov", "extapps2.oge.gov@evil.example"),
            DIRECT.replace("Example-278T.pdf", "Example-278T.pdf#fragment"),
        ]
        for type_markup in malicious:
            with self.subTest(type_markup=type_markup), self.assertRaises(OgeCatalogError):
                parse_catalog_page(payload([row(type_markup)]), start=0, length=1)

    def test_pagination_gap_overlap_and_short_page_fail_closed(self):
        first = parse_catalog_page(payload([row()], total=3), start=0, length=1)
        gap = parse_catalog_page(payload([row()], total=3), start=2, length=1)
        with self.assertRaises(OgeCatalogError):
            build_catalog([first, gap])

        overlap = parse_catalog_page(payload([row()], total=3), start=0, length=1)
        with self.assertRaises(OgeCatalogError):
            build_catalog([first, overlap])

        short = parse_catalog_page(payload([row()], total=3), start=0, length=2)
        with self.assertRaises(OgeCatalogError):
            build_catalog([short])

    def test_page_order_does_not_change_output(self):
        first = parse_catalog_page(payload([row(DIRECT)], total=2), start=0, length=1)
        second = parse_catalog_page(payload([row(REQUEST)], total=2), start=1, length=1)
        self.assertEqual(build_catalog([first, second]), build_catalog([second, first]))

    def test_records_total_is_strict(self):
        wrong_filtered = payload([row()])
        wrong_filtered["recordsFiltered"] = 0
        with self.assertRaises(OgeCatalogError):
            parse_catalog_page(wrong_filtered, start=0, length=1)
        first = parse_catalog_page(payload([row()], total=2), start=0, length=1)
        second = parse_catalog_page(payload([row()], total=3), start=1, length=1)
        with self.assertRaises(OgeCatalogError):
            build_catalog([first, second])

    def test_collection_gate_is_default_closed_and_exact(self):
        self.assertEqual(collection_gate_status(OgeSourceConfig())["status"], "disabled")
        self.assertEqual(collection_gate_status(OgeSourceConfig(enabled=True))["status"], "blocked")
        with self.assertRaises(OgeCatalogError):
            require_collection_enabled(OgeSourceConfig(enabled=True))
        config = source_config_from_environment({
            "OGE_COLLECTION_ENABLED": "true", "OGE_TERMS_ACKNOWLEDGED": "true"})
        require_collection_enabled(config)
        self.assertEqual(collection_gate_status(config)["status"], "enabled")
        with self.assertRaises(OgeCatalogError):
            source_config_from_environment({"OGE_COLLECTION_ENABLED": "TRUE"})

    def test_discovery_paginates_and_archives_without_form_201(self):
        class Client:
            def __init__(self):
                self.calls = []

            def download_page(self, *, start, length, draw):
                self.calls.append((start, length, draw))
                markup = DIRECT if start == 0 else REQUEST
                content = json.dumps(payload([row(markup)], total=2), separators=(",", ":")).encode()
                return content, {"content-type": "application/json"}

        with tempfile.TemporaryDirectory() as folder:
            client = Client()
            result = discover_catalog(
                Path(folder), OgeSourceConfig(True, True), page_size=1, client=client)
            self.assertEqual(client.calls, [(0, 1, 1), (1, 1, 2)])
            self.assertEqual(result["metadata"]["direct_pdf_count"], 1)
            self.assertEqual(result["metadata"]["request_required_count"], 1)
            self.assertTrue((Path(folder) / result["metadata"]["archive_path"]).is_file())
            normalized = Path(folder) / result["metadata"]["normalized_archive_path"]
            self.assertEqual(json.loads(normalized.read_text(encoding="utf-8"))["transactions"],
                             result["transactions"])
            self.assertEqual(len(list((Path(folder) / "oge/catalog/pages").glob("*.json"))), 2)

    def test_default_discovery_probes_total_then_fetches_one_complete_page(self):
        class Client:
            def __init__(self):
                self.calls = []

            def download_page(self, *, start, length, draw):
                self.calls.append((start, length, draw))
                rows = [row(DIRECT)] if length == 1 else [row(DIRECT), row(REQUEST)]
                content = json.dumps(payload(rows, total=2), separators=(",", ":")).encode()
                return content, {"content-type": "application/json"}

        with tempfile.TemporaryDirectory() as folder:
            client = Client()
            result = discover_catalog(Path(folder), OgeSourceConfig(True, True), client=client)
            self.assertEqual(client.calls, [(0, 1, 1), (0, 2, 2)])
            self.assertEqual(result["catalog_rows_covered"], 2)
            self.assertEqual(result["metadata"]["page_count"], 1)

    def test_default_discovery_rejects_catalog_change_after_probe(self):
        class Client:
            def download_page(self, *, start, length, draw):
                total = 2 if draw == 1 else 3
                rows = [row(DIRECT)] if draw == 1 else [row(DIRECT), row(REQUEST)]
                return json.dumps(payload(rows, total=total)).encode(), {
                    "content-type": "application/json"}

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(OgeCatalogError, "changed during collection"):
                discover_catalog(Path(folder), OgeSourceConfig(True, True), client=Client())

    def test_http_client_uses_bounded_unfiltered_datatables_get(self):
        body = json.dumps(payload([row(DIRECT)])).encode()

        class Response:
            status = 200
            headers = {"Content-Type": "application/json", "ETag": "example"}

            def __init__(self, request):
                self.request = request

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return self.request.full_url

            def read(self, _):
                return body

        class Opener:
            request = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response(request)

        opener = Opener()
        content, headers = OgeCatalogClient(timeout=7, opener=opener).download_page(
            start=0, length=100, draw=1)
        query = parse_qs(urlsplit(opener.request.full_url).query, keep_blank_values=True)
        self.assertEqual(content, body)
        self.assertEqual((query["start"], query["length"], query["search[value]"]),
                         (["0"], ["100"], [""]))
        self.assertEqual((query["order[0][column]"], query["order[0][dir]"]),
                         (["0"], ["desc"]))
        self.assertEqual(headers["etag"], "example")

    def test_http_client_retries_transient_network_failures(self):
        body = json.dumps(payload([row(DIRECT)])).encode()

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __init__(self, request):
                self.request = request

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def geturl(self):
                return self.request.full_url

            def read(self, _):
                return body

        class Opener:
            attempts = 0

            def open(self, request, timeout):
                self.attempts += 1
                if self.attempts < 3:
                    raise urllib.error.URLError("temporary")
                return Response(request)

        opener = Opener()
        delays = []
        content, _ = OgeCatalogClient(
            timeout=7, opener=opener, sleeper=delays.append).download_page(
                start=0, length=1000, draw=1)
        self.assertEqual(content, body)
        self.assertEqual(opener.attempts, 3)
        self.assertEqual(delays, [1, 2])


if __name__ == "__main__":
    unittest.main()
