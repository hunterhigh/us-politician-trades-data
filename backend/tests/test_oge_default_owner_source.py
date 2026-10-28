"""Default-reader conservation from real generated sources, not OCR accuracy."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from test_oge_owner_correction import fixture, write_files
from unison_snapshot.codec import encode
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_default_owner_conservation import prepare_default_owner_conservation


def unknown_context(raw):
    pdf = pymupdf.open(stream=raw, filetype='pdf')
    pdf[1].insert_text((30, 80), 'Additional information')
    result = pdf.tobytes()
    pdf.close()
    return result


class DefaultOwnerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.closed = fixture(asset='Example Common Stock See Endnote (EXM)', historical=True)
        cls.unknown = fixture(pdf_mutator=unknown_context)

    def inputs(self, data=None):
        old, expected, review, evidence, base = deepcopy(data or self.closed)
        raw = json.loads(next(value for path, value in review.items() if path.endswith('/oge-278t-pdf-v2.json')))
        catalog = json.loads(next(value for path, value in evidence.items() if '/catalog/normalized/' in path))
        return raw, [expected], catalog['transactions'][0], evidence

    def test_complete_alias_current_from_original_preserves_historical_classifier_and_raw_binding(self):
        raw, facts, record, evidence = self.inputs()
        self.assertEqual(facts[0]['instrument_type'], 'Bond')
        before = deepcopy((raw, facts))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_files(root, evidence)
            context = prepare_default_owner_conservation(raw, facts, record, evidence_root=root)
            self.assertEqual(context.protected_ids, {facts[0]['id']})
            self.assertEqual(context.status, 'observed')
            self.assertIsNone(context.reason)
            self.assertEqual(context.source_current_ids(facts), {facts[0]['id']})
            self.assertEqual(context.binding['source']['parser_version'], 'oge-278t-pdf/v2')
            self.assertEqual(context.binding['source']['extraction_canonical_sha256'],
                             hashlib.sha256(encode(raw)).hexdigest())
            auxiliary = context.binding['auxiliary_owner_scope']
            self.assertEqual(auxiliary['legacy'], context.binding['source'])
            self.assertEqual(auxiliary['native']['parser_version'], 'oge-278t-pdf/v7')
        self.assertEqual((raw, facts), before)

    def test_physical_intersection_protected_even_when_full_old_projection_is_unsupported(self):
        raw, facts, record, evidence = self.inputs()
        for field, value in (('instrument_type', 'Unspecified'), ('unexpected_null', None),
                             ('position_effect_basis', 'unsupported')):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                changed = [{**facts[0], field: value}]
                root = Path(directory)
                write_files(root, evidence)
                context = prepare_default_owner_conservation(raw, changed, record, evidence_root=root)
                self.assertEqual(context.protected_ids, {facts[0]['id']})
                self.assertEqual(context.source_current_ids(changed), set())

    def test_unknown_or_rootless_scope_preserves_without_current_qualification(self):
        for data, rooted in ((self.closed, False), (self.unknown, True)):
            with self.subTest(rooted=rooted), tempfile.TemporaryDirectory() as directory:
                raw, facts, record, evidence = self.inputs(data)
                root = Path(directory)
                write_files(root, evidence)
                context = prepare_default_owner_conservation(raw, facts, record,
                    evidence_root=root if rooted else None)
                self.assertEqual(context.protected_ids, {facts[0]['id']})
                self.assertEqual(context.status, 'unknown')
                self.assertEqual(context.source_current_ids(facts), set())

    def test_no_target_or_other_reader_does_not_read_original(self):
        raw, facts, record, _ = self.inputs()
        cases = [(raw, [{**facts[0], 'owner': 'Self'}]),
                 (raw, [{**facts[0], 'verification_status': 'unverified'}]),
                 ({**raw, 'parser_version': 'oge-278t-pdf/v7'}, facts)]
        with patch('unison_snapshot.oge_reports.parse_archived_pdf', side_effect=AssertionError('No source read expected')):
            for source, old in cases:
                with self.subTest(parser=source['parser_version'], old=old[0]['owner']):
                    context = prepare_default_owner_conservation(source, old, record,
                        evidence_root=Path('missing-original-root'))
                    self.assertEqual(context.protected_ids, set())
                    self.assertEqual(context.status, 'not_applicable')
            wrong_source = [{**facts[0], 'source_url': 'https://example.invalid/other'}]
            with self.assertRaisesRegex(OgeCatalogError, 'formal source URL differs'):
                prepare_default_owner_conservation(raw, wrong_source, record,
                    evidence_root=Path('missing-original-root'))

    def test_wrong_whole_cache_and_missing_original_fail_without_mutation(self):
        raw, facts, record, evidence = self.inputs()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_files(root, evidence)
            tampered = {**raw, 'owner_scope_complete': True}
            before = deepcopy((tampered, facts))
            with self.assertRaisesRegex(OgeCatalogError, 'whole-cache replay differs'):
                prepare_default_owner_conservation(tampered, facts, record, evidence_root=root)
            self.assertEqual((tampered, facts), before)
            with self.assertRaises(OgeCatalogError):
                prepare_default_owner_conservation(raw, facts, record, evidence_root=root/'missing')

    def test_pre_inventory_cache_is_readable_without_creating_missing_owner_proof(self):
        raw, facts, record, evidence = self.inputs()
        raw.pop('source_rows')
        for row in raw['transactions']:
            row.pop('cells')
        before = deepcopy(raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_files(root, evidence)
            context = prepare_default_owner_conservation(raw, facts, record, evidence_root=root)
            self.assertEqual(context.source_current_ids(facts), set())
            changed = deepcopy(raw)
            changed['transactions'][0]['amount_low'] += 1
            with self.assertRaisesRegex(OgeCatalogError, 'whole-cache replay differs'):
                prepare_default_owner_conservation(changed, facts, record, evidence_root=root)
            changed = deepcopy(raw)
            changed['source_rows'] = []
            with self.assertRaisesRegex(OgeCatalogError, 'whole-cache replay differs'):
                prepare_default_owner_conservation(changed, facts, record, evidence_root=root)
        self.assertEqual(raw, before)

    def test_duplicate_formal_ids_rejected_and_manual_effective_is_not_raw_source(self):
        raw, facts, record, _ = self.inputs()
        with self.assertRaisesRegex(OgeCatalogError, 'formal IDs are duplicated'):
            prepare_default_owner_conservation(raw, facts+facts, record)
        unresolved = {**raw, 'filed_at': None, 'evidence_complete': False,
                      'document_reasons': ['filer_signature_date_not_unique']}
        context = prepare_default_owner_conservation(raw, facts, record, source_extraction=unresolved)
        self.assertEqual(context.protected_ids, {facts[0]['id']})
        self.assertEqual(context.source_current_ids(facts), set())
        self.assertEqual(context.binding['source']['extraction_canonical_sha256'],
                         hashlib.sha256(encode(unresolved)).hexdigest())
        self.assertNotEqual(context.binding['source']['extraction_canonical_sha256'],
                            hashlib.sha256(encode(raw)).hexdigest())


if __name__ == '__main__':
    unittest.main()
