"""Normal saved-observation consumers use source page roles, not page labels."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

import pymupdf
from test_oge_native_page_roles import native_privacy
from test_oge_observed_candidate import original
from test_oge_content_relations import saved
from test_oge_candidate import direct, catalog, base, DOCUMENT_ID
from unison_snapshot.oge_transaction_adapter import materialize_oge_278t_observations, adapt_oge_278t_extraction
from unison_snapshot.oge_candidate import build_oge_candidate


class PageRoleConsumptionTests(unittest.TestCase):
    def fixture(self, folder, *, mutation=None, parser_claim=False):
        path = folder/'source.pdf'
        native_privacy(path, mutation=mutation)
        document = pymupdf.open(path)
        document.new_page(width=792, height=792)
        raw = document.tobytes(); document.close(); path.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        old = original()
        old.update(source_sha256=sha, transactions=[], quarantined=[], source_rows=[])
        if parser_claim:
            old['source_rows'] = [{'page_number': 1, 'cells': ['1', 'Conflicting parser row', '', '', '', '']}]
        observation = saved(); observation['source_sha256'] = sha
        return path, old, {'observations': [observation], 'document_page_count': 2,
                           'archived_pdf_path': str(path)}

    def test_normal_materializer_and_candidate_audit_close_only_source_proven_administrative_page(self):
        with tempfile.TemporaryDirectory() as folder:
            path, old, request = self.fixture(Path(folder))
            before = deepcopy(old)
            result = materialize_oge_278t_observations(old, direct(), **request)
            receipt = result['observation_receipt']
            self.assertEqual(old, before)
            self.assertEqual(receipt['unobserved_pages'], [])
            self.assertEqual(receipt['non_table_page_proofs'][0]['page_number'], 1)
            self.assertFalse(receipt['coverage_complete'])  # The other page has no independent row coverage.
            candidate, audit = build_oge_candidate(catalog(), [old], base(),
                data_cutoff_at='2025-06-04T00:00:00Z', saved_observations_by_document={DOCUMENT_ID: request})
            self.assertEqual(audit['reports'][0]['observation_receipt']['non_table_page_proofs'],
                             receipt['non_table_page_proofs'])
            adapted = adapt_oge_278t_extraction(old, direct(), raster_observations=request['observations'],
                                               document_page_count=2, archived_pdf_path=path)
            self.assertEqual(adapted['source_report_ledger']['pages'][0]['coverage'], 'non_table')
            self.assertEqual(adapted['source_report_ledger']['pages'][1]['coverage'], 'unknown')
            self.assertIsNone(adapted['source_report_ledger']['qualified_transaction_count'])
            repeated, _ = build_oge_candidate(catalog(), [old], candidate,
                data_cutoff_at='2025-06-04T00:00:00Z', saved_observations_by_document={DOCUMENT_ID: request})
            self.assertEqual(repeated, candidate)

    def test_extra_source_content_and_absent_original_keep_missing_page_unknown(self):
        for mutation in ('image', 'extra_text', 'invisible', 'footer_extra_run', 'footer_adjacent_extra_run'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                _, old, request = self.fixture(Path(folder), mutation=mutation)
                receipt = materialize_oge_278t_observations(old, direct(), **request)['observation_receipt']
                self.assertEqual(receipt['non_table_page_proofs'], [])
                self.assertEqual(receipt['unobserved_pages'], [1])
                request.pop('archived_pdf_path')
                receipt = materialize_oge_278t_observations(old, direct(), **request)['observation_receipt']
                self.assertEqual(receipt['non_table_page_proofs'], [])
                self.assertEqual(receipt['unobserved_pages'], [1])
                self.assertFalse(receipt['coverage_complete'])

    def test_parser_claim_on_proven_privacy_page_remains_a_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            path, old, request = self.fixture(Path(folder), parser_claim=True)
            receipt = materialize_oge_278t_observations(old, direct(), **request)['observation_receipt']
            self.assertEqual(receipt['non_table_page_proofs'], [])
            self.assertEqual(len(receipt['page_role_conflicts']), 1)
            self.assertFalse(receipt['coverage_complete'])
            adapted = adapt_oge_278t_extraction(old, direct(), raster_observations=request['observations'],
                                               document_page_count=2, archived_pdf_path=path)
            page = adapted['source_report_ledger']['pages'][0]
            self.assertEqual(page['coverage'], 'unknown')
            self.assertEqual(page['coverage_reason'], 'source_page_role_conflicts_with_parser_rows')

    def test_observed_unknown_page_without_slots_also_consumes_original_role_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            path, old, request = self.fixture(Path(folder))
            request['observations'][0]['pages'].insert(0, {
                'page_number': 1, 'column_geometry': 'unknown',
                'physical_slot_count': 0, 'slots': []})
            receipt = materialize_oge_278t_observations(old, direct(), **request)['observation_receipt']
            self.assertEqual([p['page_number'] for p in receipt['non_table_page_proofs']], [1])
            self.assertEqual(receipt['page_role_conflicts'], [])
            adapted = adapt_oge_278t_extraction(old, direct(), raster_observations=request['observations'],
                                               document_page_count=2, archived_pdf_path=path)
            self.assertEqual(adapted['source_report_ledger']['pages'][0]['coverage'], 'non_table')
            self.assertFalse(receipt['coverage_complete'])

    def test_physical_slot_claim_conflicts_without_erasing_the_logical_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path, old, request = self.fixture(Path(folder))
            page = deepcopy(request['observations'][0]['pages'][0]); page['page_number'] = 1
            request['observations'][0]['pages'].insert(0, page)
            receipt = materialize_oge_278t_observations(old, direct(), **request)['observation_receipt']
            self.assertEqual(receipt['non_table_page_proofs'], [])
            self.assertEqual([p['page_number'] for p in receipt['page_role_conflicts']], [1])
            self.assertFalse(receipt['coverage_complete'])
            adapted = adapt_oge_278t_extraction(old, direct(), raster_observations=request['observations'],
                                               document_page_count=2, archived_pdf_path=path)
            ledger = adapted['source_report_ledger']
            self.assertEqual(ledger['pages'][0]['coverage'], 'unknown')
            self.assertEqual(len(ledger['pages'][0]['slots']), 1)
            self.assertTrue(any(ref['page_number'] == 1 for row in ledger['logical_rows'] for ref in row['slot_refs']))
