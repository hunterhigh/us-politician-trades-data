import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_whitehouse_278t_review.py"
SPEC = importlib.util.spec_from_file_location("whitehouse_278t_review_identity_diagnostic", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def inputs():
    url = "https://www.whitehouse.gov/wp-content/uploads/2025/11/Zinberg-Joel-Periodic-Transaction-Report.pdf"
    document_id = "wh-url:example"
    audit = [{
        "document_id": document_id,
        "document_reasons": ["catalog_filer_identity_missing"],
        "source_row_count": 2,
        "rows": [
            {"reasons": ["catalog_filer_identity_missing"]},
            {"reasons": ["catalog_filer_identity_missing", "transaction_date_invalid"]},
        ],
    }]
    coverage = [{"document_id": document_id, "filer_name_from_label": "Zinberg Joel"}]
    directory = [{
        "source_document_id": document_id, "document_url": url,
        "document_type_from_label": "278t", "source_section": "Transaction Reports",
        "filer_name_from_label": "Zinberg Joel",
    }]
    extractions = [{
        "document_id": document_id, "source_url": url, "source_sha256": "a" * 64,
        "pdf_filer_name": "Zinberg, Joel M", "pdf_position_title": "Special Assistant",
        "pdf_agency_label": "White House",
    }]
    candidate = {"people": []}
    return audit, coverage, directory, extractions, candidate


class WhiteHouse278TIdentityDiagnosticTests(unittest.TestCase):
    def test_directory_bound_rows_remain_unqualified_without_person_id(self):
        result = MODULE._identity_mapping_diagnostic(*inputs())
        group = result["person_groups"][0]
        self.assertEqual(result["identity_only_row_count"], 1)
        self.assertEqual(group["directory_pdf_aligned_report_count"], 1)
        self.assertEqual(group["identity_mapping_state"], "new_person_id_unresolved")
        self.assertFalse(result["qualification_changed"])

    def test_existing_person_id_is_reused_in_diagnostic(self):
        values = inputs()
        values[-1]["people"].append({
            "id": "oge:existing", "display_name": "Zinberg, Joel M"})
        group = MODULE._identity_mapping_diagnostic(*values)["person_groups"][0]
        self.assertEqual(group["existing_candidate_person_ids"], ["oge:existing"])
        self.assertEqual(group["identity_mapping_state"], "existing_id_reuse_candidate")

    def test_missing_directory_entry_fails_closed(self):
        values = inputs()
        values[2].clear()
        with self.assertRaisesRegex(RuntimeError, "missing the official directory"):
            MODULE._identity_mapping_diagnostic(*values)

    def test_mismatched_directory_name_stays_unresolved(self):
        values = inputs()
        values[2][0]["filer_name_from_label"] = "Other Person"
        group = MODULE._identity_mapping_diagnostic(*values)["person_groups"][0]
        self.assertEqual(group["identity_mapping_state"], "directory_pdf_unresolved")
        self.assertEqual(group["directory_pdf_aligned_report_count"], 0)


if __name__ == "__main__":
    unittest.main()
