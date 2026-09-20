from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.oge import OgeCatalogError, build_catalog, parse_catalog_page


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


if __name__ == "__main__":
    unittest.main()
