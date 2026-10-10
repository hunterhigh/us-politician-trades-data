import unittest

from unison_snapshot.whitehouse_directory_identity_shadow import bind_directory_entry


URL = "https://www.whitehouse.gov/wp-content/uploads/2025/11/Zinberg-Joel-Periodic-Transaction-Report.pdf"
EXTRACTION = {"source_url": URL, "source_sha256": "a" * 64,
              "document_id": "wh-url:example", "pdf_filer_name": "Zinberg, Joel M",
              "pdf_position_title": "Special Assistant"}
DIRECTORY = {"document_url": URL, "source_document_id": "wh-url:example",
             "document_type_from_label": "278t", "source_section": "Transaction Reports",
             "filer_name_from_label": "Zinberg Joel"}


class WhiteHouseDirectoryIdentityShadowTests(unittest.TestCase):
    def test_middle_initial_and_surname_first_label_bind_without_person_promotion(self):
        result = bind_directory_entry(EXTRACTION, DIRECTORY)
        self.assertEqual(result["status"], "directory_pdf_name_aligned")
        self.assertIsNone(result["canonical_person_id"])
        self.assertIsNone(result["qualification"])

    def test_unmatched_name_remains_unknown(self):
        result = bind_directory_entry(EXTRACTION,
                                      DIRECTORY | {"filer_name_from_label": "Other Person"})
        self.assertEqual(result["status"], "identity_unknown")

    def test_wrong_url_or_report_type_is_rejected(self):
        with self.assertRaises(ValueError):
            bind_directory_entry(EXTRACTION, DIRECTORY | {"document_url": URL + "-other"})
        with self.assertRaises(ValueError):
            bind_directory_entry(EXTRACTION,
                                 DIRECTORY | {"document_type_from_label": "278e_annual"})
