from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.senate import (
    SenateEfdError,
    SenateSourceConfig,
    build_discovery,
    normalize_transaction_type,
    parse_search_page,
    require_collection_enabled,
)


PTR_ID = "12345678-1234-4234-8234-1234567890ab"
PAPER_ID = "87654321-4321-4321-8321-ba0987654321"


def row(kind: str = "ptr", document_id: str = PTR_ID) -> list[str]:
    return [
        "Ada",
        "Example",
        "United States Senator from California",
        f'<a href="/search/view/{kind}/{document_id}/">Periodic Transaction Report</a>',
        "09/19/2026",
    ]


def payload(rows: list[list[str]], total: int | None = None) -> dict:
    count = len(rows) if total is None else total
    return {"draw": 1, "recordsTotal": count, "recordsFiltered": count, "data": rows}


class SenateDiscoveryTests(unittest.TestCase):
    def test_source_gate_is_disabled_and_requires_both_explicit_flags(self):
        with self.assertRaisesRegex(SenateEfdError, "disabled"):
            require_collection_enabled()
        with self.assertRaisesRegex(SenateEfdError, "terms"):
            require_collection_enabled(SenateSourceConfig(enabled=True))
        require_collection_enabled(SenateSourceConfig(enabled=True, terms_acknowledged=True))

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
            official[3].replace("/\">", "/?download=1\">"),
            official[3].replace("/\">", "/#fragment\">"),
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
