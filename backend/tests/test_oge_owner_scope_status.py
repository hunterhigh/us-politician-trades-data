"""Current owner qualification versus conserved publication on real native PDFs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from test_oge_candidate import base as empty_base
from test_oge_owner_correction import fixture, write_files, encoded, DOCUMENT, CUTOFF
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.disclosure_status_oge import build_oge_status

EXPLICIT_NEW = [['', 'Filer assets', '', '', '', ''],
                ['2', 'INDEPENDENT Fund', 'Purchase', '06/02/2026', 'No', '$15,001 - $50,000']]


def unknown_external_text(raw):
    pdf = pymupdf.open(stream=raw, filetype='pdf')
    pdf[1].insert_text((30, 80), 'Additional information')
    result = pdf.tobytes(); pdf.close()
    return result


class OwnerScopeStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A harmless empty physical row lets the common source fixture prepare
        # both the approved and deferred proposal. Product behavior is asserted
        # below using the actual normal source receiver and status builder.
        cls.closed = fixture(heading=' ', extra_rows=EXPLICIT_NEW)
        cls.unknown = fixture(heading=' ', extra_rows=EXPLICIT_NEW,
                              pdf_mutator=unknown_external_text)

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='oge-owner-status-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.evidence, self.review = self.root/'evidence', self.root/'review'

    def inputs(self, *, unknown=False):
        old, expected, review, evidence, previous = deepcopy(self.unknown if unknown else self.closed)
        write_files(self.evidence, evidence); write_files(self.review, review)
        catalog_path = next(path for path in evidence if '/catalog/normalized/' in path)
        catalog = json.loads(evidence[catalog_path])
        legacy = json.loads(next(raw for path, raw in review.items() if path.endswith('pdf-v2.json')))
        native = json.loads(next(raw for path, raw in review.items() if path.endswith('pdf-v7.json')))
        return old, previous, catalog, legacy, native

    def candidate(self, values, *, generic=True, previous=None, roots=True):
        old, base, catalog, legacy, native = values
        candidate, audit = build_oge_candidate(catalog, [native if generic else legacy],
            base if previous is None else previous, data_cutoff_at=CUTOFF,
            evidence_root=self.evidence if roots else None,
            **({'legacy_extractions_by_document':{DOCUMENT:legacy}} if generic else {}))
        self.save_audit(audit)
        return candidate, audit

    def save_audit(self, audit):
        target = self.review/'oge/qualifications/current.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded(audit))

    def status(self, candidate):
        return next(row for scope in build_oge_status(self.evidence, self.review,
                    candidate['transactions'], []) for row in scope['reports']
                    if row['report_id'] == DOCUMENT)

    def test_unknown_scope_conserves_complete_prior_and_qualifies_explicit_new_row(self):
        values = self.inputs(unknown=True)
        old = values[0]
        result, audit = self.candidate(values)
        self.assertIn(old, result['transactions'])
        new = next(row for row in result['transactions'] if row['id'] != old['id'])
        self.assertEqual(new['owner'], 'Self')
        record = self.status(result)
        self.assertEqual(record['qualification_state'], 'partial')
        self.assertEqual(record['qualified_transaction_count'], 1)
        self.assertEqual(set(record['publication_transaction_ids']), {old['id'], new['id']})
        self.assertIn('owner_migration_source_scope_unknown', record['reason_codes'])
        repeated, _ = self.candidate(values, previous=result)
        self.assertEqual(repeated['transactions'], result['transactions'])
        self.assertEqual(self.status(repeated)['qualified_transaction_count'], 1)

    def test_closed_scope_and_real_next_base_requalify_the_same_legacy_id(self):
        values = self.inputs()
        old = values[0]
        result, _ = self.candidate(values)
        self.assertIn({**old, 'owner':'Undisclosed'}, result['transactions'])
        first = self.status(result)
        self.assertEqual(first['qualified_transaction_count'], 2)
        self.assertEqual(first['qualification_state'], 'qualified')
        repeated, _ = self.candidate(values, previous=result)
        self.assertEqual(repeated['transactions'], result['transactions'])
        self.assertEqual(self.status(repeated)['qualified_transaction_count'], 2)
        self.assertEqual(self.status(repeated)['publication_transaction_ids'], first['publication_transaction_ids'])

    def test_default_selection_is_not_replaced_by_a_coexisting_generic_cache(self):
        values = self.inputs(unknown=True)
        result, audit = self.candidate(values, generic=False)
        record = self.status(result)
        self.assertEqual(record['source_interpretation_binding']['parser_version'], 'oge-278t-pdf/v2')
        self.assertEqual(record['qualified_transaction_count'], 2)
        self.assertEqual(record['qualification_state'], 'qualified')
        self.assertFalse(any('owner_scope' in reason for reason in record['reason_codes']))
        audit['reports'][0].pop('source_interpretation_binding')
        self.save_audit(audit)
        missing = self.status(result)
        self.assertEqual(missing['qualification_state'], 'unknown')
        self.assertIsNone(missing['qualified_transaction_count'])
        self.assertIn('source_interpretation_selection_unknown', missing['reason_codes'])
        self.assertEqual(missing['publication_transaction_ids'], record['publication_transaction_ids'])

    def test_unknown_deferred_ids_cannot_be_added_to_a_self_signed_current_set(self):
        values = self.inputs(unknown=True)
        result, audit = self.candidate(values)
        report = audit['reports'][0]
        self.assertEqual(len(report['owner_scope_view']['current_ids']), 1)
        report['owner_scope_view']['current_ids'] = sorted(row['id'] for row in result['transactions'])
        self.save_audit(audit)
        with self.assertRaisesRegex(ValueError, 'identities differ'):
            self.status(result)

    def test_wrong_full_cache_hash_source_and_selected_family_fail_before_status(self):
        values = self.inputs()
        result, audit = self.candidate(values)
        original_review = {path.relative_to(self.review):path.read_bytes()
                           for path in self.review.rglob('*') if path.is_file()}
        original_source = {path.relative_to(self.evidence):path.read_bytes()
                           for path in self.evidence.rglob('*') if path.is_file()}
        from unison_snapshot.oge_native_owner_scope import source_interpretation_binding
        for kind in ('binding', 'cache', 'source', 'metadata', 'receiver'):
            with self.subTest(kind=kind):
                write_files(self.review, original_review); write_files(self.evidence, original_source)
                changed = deepcopy(audit)
                report = changed['reports'][0]
                selected = report['source_interpretation_binding']
                if kind == 'binding': selected['extraction_canonical_sha256'] = '0'*64
                elif kind == 'cache':
                    path = self.review/'oge/extractions'/DOCUMENT/selected['source_sha256']/'oge-278t-pdf-v7.json'
                    value = json.loads(path.read_bytes()); value['transactions'][0]['amount_low'] = 1002
                    path.write_bytes(encoded(value))
                    report['source_interpretation_binding'] = source_interpretation_binding(value)
                elif kind == 'source':
                    path = self.evidence/f'oge/reports/{DOCUMENT}/{selected["source_sha256"]}.pdf'
                    path.write_bytes(path.read_bytes()+b'\nchanged original\n')
                elif kind == 'metadata':
                    path = self.evidence/f'oge/reports/{DOCUMENT}/{selected["source_sha256"]}.json'
                    value = json.loads(path.read_bytes()); value['document_url'] = 'https://example.invalid/wrong.pdf'
                    path.write_bytes(encoded(value))
                else:
                    report['source_interpretation_binding'] = source_interpretation_binding(values[3])
                self.save_audit(changed)
                with self.assertRaises((ValueError, OgeCatalogError)):
                    self.status(result)

    def test_no_root_pure_projection_defers_owner_without_blocking_explicit_new_fact(self):
        values = self.inputs()
        result, _ = self.candidate(values, roots=False)
        self.assertIn(values[0], result['transactions'])
        self.assertEqual(len(result['transactions']), 2)
        record = self.status(result)
        self.assertEqual(record['qualified_transaction_count'], 1)
        self.assertEqual(record['qualification_state'], 'partial')

    def test_nonordinary_amount_receiver_keeps_its_independent_qualification(self):
        """Combination boundary, not a saved OCR/source recognition claim."""
        values = self.inputs()
        result, audit = self.candidate(values)
        old_id = values[0]['id']
        new_id = next(row['id'] for row in result['transactions'] if row['id'] != old_id)
        from unison_snapshot.oge_278t_fields import LEGACY_AMOUNT_INTERPRETATION_MODE as mode
        report = audit['reports'][0]
        report.pop('owner_scope_view')  # The final receiver is not ordinary native.
        report.update(field_interpretation_mode=mode,
            amount_recompute_current_qualified_ids=[new_id],
            amount_recompute_retained_formal_ids=[old_id],
            amount_recompute_published_ids=sorted([old_id, new_id]),
            owner_scope_current_qualified_ids=[new_id], quarantined_count=1,
            quarantined=[{'extraction_id':old_id,
                'reasons':['legacy_amount_shape_current_unresolved']}],
            observation_receipt={'schema_version':'oge-278t-observed-facts/v1',
                'coverage_complete':False, 'field_interpretation_mode':mode,
                'protected_formal_reference_ids':[old_id], 'interpretation_conflicts':[], 'rows':[]})
        self.save_audit(audit)
        with patch('unison_snapshot.oge_native_owner_scope.NativeOwnerScope.ordinary_native_view',
                   side_effect=AssertionError('saved receiver is not an ordinary native view')):
            record = self.status(result)
        self.assertEqual(record['qualified_transaction_count'], 1)
        self.assertEqual(record['qualification_state'], 'partial')
        self.assertEqual(set(record['publication_transaction_ids']), {old_id, new_id})
        report['owner_scope_current_qualified_ids'] = sorted([old_id, new_id])
        self.save_audit(audit)
        with self.assertRaisesRegex(ValueError, 'identities do not close'):
            self.status(result)

    def test_default_manual_date_uses_raw_binding_before_existing_sidecar_layer(self):
        # Existing manual-date compatibility, not a date recognition experiment:
        # an explicit visible certification date lacks the native signature
        # phrase consumed by v2 and is supplied through the established sidecar.
        from unison_snapshot.oge_reports import parse_archived_pdf
        from unison_snapshot.oge_manual_dates import apply_manual_cover_dates
        from unison_snapshot.oge_native_owner_scope import source_interpretation_binding
        _, _, review, evidence, _ = deepcopy(self.closed)
        source = next(raw for path, raw in evidence.items() if path.endswith('.pdf'))
        pdf = pymupdf.open(stream=source, filetype='pdf')
        pdf.delete_page(0)
        page = pdf.new_page(pno=0, width=792, height=792)
        page.insert_text((30,40), "OGE Form 278-T\nFiler's Certification\nFiler's Signature\nDate: 06/13/2026")
        raw = pdf.tobytes(); pdf.close()
        sha = hashlib.sha256(raw).hexdigest()
        self.evidence, self.review = self.root/'manual-evidence', self.root/'manual-review'
        write_files(self.evidence, {path:value for path,value in evidence.items() if '/catalog/' in path})
        write_files(self.review, {'status/oge.json':review['status/oge.json']})
        metadata = json.loads(next(value for path,value in evidence.items()
                                   if '/reports/' in path and path.endswith('.json')))
        relative = f'oge/reports/{DOCUMENT}/{sha}.pdf'
        metadata.update(sha256=sha, byte_length=len(raw), archive_path=relative)
        write_files(self.evidence, {relative:raw, relative[:-4]+'.json':encoded(metadata)})
        report = parse_archived_pdf(self.evidence, self.evidence/(relative[:-4]+'.json'))
        self.assertIsNone(report['filed_at'])
        write_files(self.review, {f'oge/extractions/{DOCUMENT}/{sha}/oge-278t-pdf-v2.json':encoded(report)})
        sidecar = {'schema_version':'oge-manual-cover-date/v1', 'status':'observation_only',
            'qualification_effect':'none', 'document_id':DOCUMENT, 'source_sha256':sha,
            'source_url':report['source_url'], 'extraction_parser_version':report['parser_version'],
            'page_number':1, 'recognition_method':'human_visual_reading_of_pdf_cover',
            'field_role':'filer_signature_date', 'raw_reading':'06/13/2026',
            'observed_date':'2026-06-13', 'filed_at':'2026-06-13'}
        write_files(self.review, {f'oge/manual_cover_dates/{DOCUMENT}/{sha}.json':encoded(sidecar)})
        effective = apply_manual_cover_dates([report], self.review/'oge/manual_cover_dates')[0]
        catalog = json.loads(next(value for path,value in evidence.items() if '/catalog/normalized/' in path))
        candidate, audit = build_oge_candidate(catalog, [effective], empty_base(), data_cutoff_at=CUTOFF,
            evidence_root=self.evidence, source_extractions_by_document={DOCUMENT:report})
        self.assertEqual(audit['reports'][0]['source_interpretation_binding'],
                         source_interpretation_binding(report))
        self.assertNotEqual(source_interpretation_binding(report), source_interpretation_binding(effective))
        self.save_audit(audit)
        record = self.status(candidate)
        self.assertEqual(record['qualified_transaction_count'], 2)
        self.assertEqual(record['filed_date'], '2026-06-13')


if __name__ == '__main__':
    unittest.main()
