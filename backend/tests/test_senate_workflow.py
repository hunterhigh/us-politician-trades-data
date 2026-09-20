from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SenateWorkflowTests(unittest.TestCase):
    def test_catalog_collection_is_double_gated_and_default_closed(self):
        content = (ROOT / ".github/workflows/senate-efd.yml").read_text(encoding="utf-8")
        self.assertIn("SENATE_EFD_COLLECTION_ENABLED: ${{ vars.SENATE_EFD_COLLECTION_ENABLED }}", content)
        self.assertIn("SENATE_EFD_TERMS_ACKNOWLEDGED: ${{ vars.SENATE_EFD_TERMS_ACKNOWLEDGED }}", content)
        self.assertIn("needs: evaluate-gate", content)
        self.assertIn("needs.evaluate-gate.outputs.status == 'enabled'", content)
        self.assertIn("python -m unison_snapshot senate-efd-gate", content)
        self.assertIn("python -m unison_snapshot discover-senate-efd", content)
        self.assertNotIn("curl ", content)
        self.assertNotIn("wget ", content)

    def test_catalog_respects_evidence_review_and_state_boundaries(self):
        content = (ROOT / ".github/workflows/senate-efd.yml").read_text(encoding="utf-8")
        self.assertIn("senate_efd/catalog", content)
        self.assertIn("senate_efd/discoveries", content)
        self.assertIn("status/senate_efd.json", content)
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("group: disclosure-review-writer", content)
        self.assertIn("reports': {'status': 'not_collected'", content)

    def test_roster_refresh_preserves_catalog_gate_and_status(self):
        content = (ROOT / ".github/workflows/senate-roster.yml").read_text(encoding="utf-8")
        self.assertIn("gate = prior.get('gate'", content)
        self.assertIn("for key in ('catalog', 'reports')", content)
        self.assertIn("'collection_enabled': gate['collection_enabled']", content)
        self.assertIn("'terms_acknowledged': gate['terms_acknowledged']", content)


if __name__ == "__main__":
    unittest.main()
