"""Aligned image rules cannot attest report content; fixed readings are not OCR gold."""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

import pymupdf
from PIL import Image, ImageDraw

from test_oge_candidate import direct, catalog, base, DOCUMENT_ID
from test_oge_content_relations import saved
from test_oge_observed_candidate import original
from unison_snapshot.oge_278t_raster_slots import _observe_slot_columns
from unison_snapshot.oge_transaction_adapter import materialize_oge_278t_observations, adapt_oge_278t_extraction
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.disclosure_status_oge import _record, _apply_audit


class CompletenessAttestationTests(unittest.TestCase):
    def fixture(self, folder, *, extra=False):
        edges = [90, 135, 445, 496, 553, 600, 700]
        old = original()
        rows = [deepcopy(old['transactions'][0]['cells'])] + [
            [str(i), 'ALPHA', 'purchase', '05/02/2025', 'No', '$1,001 - $15,000'] for i in range(2, 9)]
        image = Image.new('RGB', (1584, 1584), 'white'); draw = ImageDraw.Draw(image)
        for y in range(100, 221, 15):
            draw.line((180, y*2, 1400, y*2), fill='black', width=2)
        for x in edges:
            draw.line((x*2, 200, x*2, 440), fill='black', width=2)
        if extra:
            # This content has no horizontal rule and does not intersect any
            # audit strip. A rule audit must not pretend to have read it.
            draw.text((700, 1100), 'SALE ACME 05/01/2025 $50,001-$100,000', fill='black')
        stream = BytesIO(); image.save(stream, format='PNG')
        document = pymupdf.open(); page = document.new_page(width=792, height=792)
        page.insert_image(page.rect, stream=stream.getvalue())
        raw = document.tobytes(); document.close()
        sha = hashlib.sha256(raw).hexdigest(); path = folder/'source.pdf'; path.write_bytes(raw)
        old['source_sha256'] = sha
        data = saved(rows); data['source_sha256'] = sha
        page = data['pages'][0]; page.update(page_number=1, column_edges_pdf_points=edges)
        for index, slot in enumerate(page['slots']):
            top = 100 + index*15
            words = [{'text': text, 'bbox_pdf_points': [edges[i]+1, top+2, edges[i+1]-1, top+8]}
                     for i, text in enumerate(rows[index]) if text]
            slot.update(words=words, **_observe_slot_columns(words, edges))
        return path, old, {'observations': [data], 'document_page_count': 1, 'archived_pdf_path': path}

    def test_aligned_rules_remain_positive_geometry_but_not_complete_report_content(self):
        for extra in (False, True):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temporary:
                path, old, request = self.fixture(Path(temporary), extra=extra)
                before = deepcopy(old)
                materialized = materialize_oge_278t_observations(old, direct(), **request)
                self.assertEqual(old, before)
                self.assertEqual(materialized['transactions'][0], old['transactions'][0])
                receipt = materialized['observation_receipt']
                self.assertTrue(receipt['raster_source_inventory']['all_selected_boundaries_accounted'])
                self.assertFalse(receipt['raster_source_inventory']['report_completeness_attested'])
                self.assertFalse(receipt['coverage_complete'])
                self.assertEqual(receipt['unresolved_slot_count'], 0)
                self.assertEqual(len(materialized['transactions']), 8)
                adapted = adapt_oge_278t_extraction(old, direct(), raster_observations=request['observations'],
                    document_page_count=1, archived_pdf_path=path)
                ledger = adapted['source_report_ledger']
                self.assertEqual(ledger['observed_physical_slot_count'], 8)
                self.assertIsNone(ledger['physical_slot_count'])
                self.assertEqual(ledger['logical_row_count'], 8)
                self.assertEqual(ledger['pages'][0]['coverage'], 'unknown')
                self.assertEqual(ledger['pages'][0]['coverage_reason'], 'independent_source_content_unverified')
                self.assertEqual(ledger['relation_coverage'], 'partial_unknown')

    def test_normal_candidate_status_and_repeat_keep_facts_without_signing_full_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, old, request = self.fixture(Path(temporary), extra=True)
            source_before = request['archived_pdf_path'].read_bytes()
            candidate, audit = build_oge_candidate(catalog(), [old], base(), data_cutoff_at='2025-06-04T00:00:00Z',
                saved_observations_by_document={DOCUMENT_ID: request})
            self.assertEqual(len(candidate['transactions']), 8)
            self.assertIn(old['transactions'][0]['extraction_id'], {row['id'] for row in candidate['transactions']})
            record = _record(DOCUMENT_ID, direct()['document_url'], '278t')
            record['source_versions'] = [old['source_sha256']]
            _apply_audit(record, audit['reports'][0])
            self.assertEqual(record['qualification_state'], 'partial')
            self.assertEqual(record['qualified_transaction_count'], 8)
            self.assertIn('observed_source_coverage_incomplete', record['reason_codes'])
            repeated, repeated_audit = build_oge_candidate(catalog(), [old], candidate,
                data_cutoff_at='2025-06-04T00:00:00Z', saved_observations_by_document={DOCUMENT_ID: request})
            self.assertEqual(repeated, candidate)
            self.assertEqual(repeated_audit['reports'][0]['observation_receipt'],
                             audit['reports'][0]['observation_receipt'])
            self.assertEqual(request['archived_pdf_path'].read_bytes(), source_before)
