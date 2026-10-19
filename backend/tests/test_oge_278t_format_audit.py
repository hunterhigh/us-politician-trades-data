"""Saved geometry evidence enters the existing audit, never qualification."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.oge_278t_format_audit import audit_278t_format


class OgeFormatGeometryIntegrationTests(unittest.TestCase):
    def audit(self, *, row_status):
        page = {"page_number": 1, "text_mode": "raster", "row_basis": "unknown",
                "candidate_row_bands": [], "coverage": "coverage_unknown_raster",
                "unresolved_row_bands": [], "unresolved_row_regions": [],
                "number_sequence_warnings": [], "row_coverage": "unknown"}
        geometry = {"status": "confirmed", "basis": "same_page_vector_and_pixel_rules",
                    "edges_pdf_points": [103, 148, 458, 509, 566, 613, 695],
                    "weak_rule_observations": [{"status": "confirmed"}]}
        census = {"results": [{"page_number": 1, "status": row_status,
                               "physical_row_bands": [[110, 125]] if row_status ==
                               "physical_slots_located" else [],
                               "column_geometry_observation": geometry}]}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "synthetic-input.bin"
            source.write_bytes(b"synthetic bytes; PDF observers are mocked")
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch("unison_snapshot.oge_278t_format_audit.inspect_pdf",
                       return_value={"page_count": 2, "pages": [page]}), patch(
                    "unison_snapshot.oge_278t_format_audit.census_with_2024_fallback",
                    return_value=(census, {1: geometry["edges_pdf_points"]})):
                return audit_278t_format(source, extraction={"source_sha256": sha,
                    "source_rows": [], "transactions": [], "quarantined": []},
                    page_numbers=[1], expected_sha256=sha, raster_census=True), geometry

    def test_column_evidence_is_retained_when_row_coverage_is_unknown(self):
        audit, geometry = self.audit(row_status="coverage_unknown")
        page = audit["pages"][0]
        self.assertEqual(page["raster_column_geometry_observation"], geometry)
        self.assertIsNone(page["physical_slot_count"])
        self.assertIn("raster_physical_coverage_unknown", page["issues"])
        self.assertEqual(audit["page_count"], 2)
        self.assertFalse(audit["production_mutation_authorized"])

    def test_confirmed_geometry_does_not_create_a_parsed_transaction(self):
        audit, geometry = self.audit(row_status="physical_slots_located")
        page = audit["pages"][0]
        self.assertEqual(page["physical_slot_count"], 1)
        self.assertEqual(page["parsed_row_count"], 0)
        self.assertEqual(page["raster_column_geometry_observation"], geometry)
        self.assertIn("physical_slot_and_selected_row_count_differ", page["issues"])
        self.assertFalse(audit["production_mutation_authorized"])


if __name__ == "__main__":
    unittest.main()
