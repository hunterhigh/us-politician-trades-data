import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house import (HouseFilingCandidate, HouseIndexError, archive_discovery,
                                   archive_document, document_url, parse_index)


def fixture(rows: str, *, extra: str | None = None) -> bytes:
    xml = f'<?xml version="1.0"?><FinancialDisclosure>{rows}</FinancialDisclosure>'.encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("2026FD.xml", xml)
        archive.writestr("2026FD.txt", "header\n")
        if extra:
            archive.writestr(extra, "bad")
    return output.getvalue()


ROW = """<Member><Prefix>Hon.</Prefix><Last>Example</Last><First>Ada</First><Suffix></Suffix>
<FilingType>P</FilingType><StateDst>CA01</StateDst><Year>2026</Year>
<FilingDate>9/17/2026</FilingDate><DocID>20000001</DocID></Member>"""


class HouseIndexTests(unittest.TestCase):
    def test_ptr_discovery_is_official_raw_not_a_transaction(self):
        rows = parse_index(fixture(ROW), 2026)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].verification_status, "official_raw_unparsed")
        self.assertEqual(rows[0].filed_date, "2026-09-17")
        self.assertEqual(rows[0].document_url,
            "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20000001.pdf")

    def test_non_ptr_uses_financial_pdf_location(self):
        rows = parse_index(fixture(ROW.replace("<FilingType>P", "<FilingType>A")), 2026)
        self.assertIn("/financial-pdfs/", rows[0].document_url)

    def test_official_blank_filing_date_is_preserved_as_unknown(self):
        row = ROW.replace("<FilingType>P", "<FilingType>W").replace(
            "<FilingDate>9/17/2026</FilingDate>", "<FilingDate></FilingDate>")
        rows = parse_index(fixture(row), 2026)
        self.assertIsNone(rows[0].filed_date)
        self.assertEqual(rows[0].verification_status, "official_raw_unparsed")

    def test_malformed_duplicate_and_unexpected_archive_fail(self):
        cases = [
            fixture(ROW + ROW),
            fixture(ROW.replace("9/17/2026", "2/30/2026")),
            fixture(ROW.replace("<Year>2026", "<Year>2025")),
            fixture(ROW, extra="../escape"),
        ]
        for archive in cases:
            with self.subTest(), self.assertRaises(HouseIndexError):
                parse_index(archive, 2026)

    def test_archive_is_content_addressed_and_repeatable(self):
        content = fixture(ROW)
        rows = parse_index(content, 2026)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = archive_discovery(root, 2026, content, {"etag": "test"}, rows,
                                      retrieved_at="2026-09-18T00:00:00+00:00")
            second = archive_discovery(root, 2026, content, {"etag": "test"}, rows,
                                       retrieved_at="2026-09-19T00:00:00+00:00")
            self.assertEqual(first, second)
            self.assertEqual(second["retrieved_at"], "2026-09-18T00:00:00+00:00")
            self.assertTrue((root / first["archive_path"]).is_file())

    def test_ptr_archive_is_content_addressed_and_stays_unparsed(self):
        candidate = parse_index(fixture(ROW), 2026)[0]
        content = b"%PDF-1.7\ncontrolled fixture\n%%EOF\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = archive_document(root, candidate, content, {"content-type": "application/pdf"},
                                     retrieved_at="2026-09-18T00:00:00+00:00")
            second = archive_document(root, candidate, content, {},
                                      retrieved_at="2026-09-19T00:00:00+00:00")
            self.assertEqual(first, second)
            self.assertEqual(first["verification_status"], "official_raw_unparsed")
            self.assertTrue((root / first["archive_path"]).is_file())

    def test_ptr_archive_rejects_non_pdf_and_unconfirmed_candidate(self):
        candidate = parse_index(fixture(ROW), 2026)[0]
        bad_candidate = HouseFilingCandidate(**{
            **candidate.__dict__, "document_url": document_url(2026, "P", "99999999")})
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(HouseIndexError):
                archive_document(Path(temporary), candidate, b"not a pdf", {})
            with self.assertRaises(HouseIndexError):
                archive_document(Path(temporary), bad_candidate, b"%PDF-1.7\n%%EOF", {})


if __name__ == "__main__":
    unittest.main()
