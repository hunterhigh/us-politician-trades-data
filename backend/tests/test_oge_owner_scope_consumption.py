"""Receiver regressions for source-proved aliases and full predecessor objects."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

from test_oge_owner_correction import fixture, write_files, DOCUMENT, CUTOFF
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.whitehouse_native_correction import _historical_projection


def outside_note(raw):
    pdf = pymupdf.open(stream=raw, filetype='pdf')
    pdf[1].insert_text((30, 80), 'Additional information')
    result = pdf.tobytes()
    pdf.close()
    return result


class NativeOwnerScopeConsumptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extra = [['', 'Filer assets', '', '', '', ''],
                 ['2', 'Independent Stock See Endnote (IND)', 'Purchase', '06/02/2026', 'No', '$15,001 - $50,000']]
        cls.closed = fixture(extra_rows=extra)
        cls.unknown = fixture(extra_rows=extra, pdf_mutator=outside_note)

    def consume(self, data, *, edit=None, corrected=False, explicit_history=False, edit_person=None):
        old, expected, review, evidence, base = deepcopy(data)
        if corrected:
            base['transactions'] = [expected]
        if edit:
            edit(base['transactions'][0])
        if edit_person:
            edit_person(base['people'][0])
        actual = deepcopy(base['transactions'][0])
        catalog = json.loads(next(raw for path, raw in evidence.items()
                                  if '/catalog/normalized/' in path))
        legacy = json.loads(next(raw for path, raw in review.items() if path.endswith('pdf-v2.json')))
        native = json.loads(next(raw for path, raw in review.items() if path.endswith('pdf-v7.json')))
        if explicit_history:
            explicit = _historical_projection(legacy, legacy['transactions'][1], actual['person_id'])
            self.assertEqual(explicit['owner'], 'Self')
            self.assertEqual(explicit['instrument_type'], 'Bond')
            base['transactions'].append(explicit)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_files(root, evidence)
            result, audit = build_oge_candidate(catalog, [native], base,
                data_cutoff_at=CUTOFF, evidence_root=root,
                legacy_extractions_by_document={DOCUMENT: legacy})
        return actual, result, audit['reports'][0]

    def test_explicit_legacy_classification_requires_its_own_source_proof(self):
        for data in (self.closed, self.unknown):
            with self.subTest(unknown=data is self.unknown):
                actual, result, report = self.consume(data, explicit_history=True)
                independent = next(row for row in result['transactions'] if row['id'] != actual['id'])
                legacy = json.loads(next(raw for path, raw in data[2].items()
                                         if path.endswith('pdf-v2.json')))
                prior = _historical_projection(legacy, legacy['transactions'][1], actual['person_id'])
                expected = {**prior, 'instrument_type': 'Stock'} if data is self.closed else prior
                self.assertEqual(independent, expected)
                current = report['owner_scope_view']['current_ids']
                classification = report['native_classification']
                if data is self.closed:
                    self.assertIn(independent['id'], current)
                    self.assertIn(independent['id'], classification['current_ids'])
                else:
                    self.assertNotIn(independent['id'], current)
                    self.assertIn(independent['id'], classification['deferred_ids'])
                self.assertEqual(report['legacy_inheritance']['unbound_formal_ids'], [])

    def test_corrected_physical_alias_does_not_block_an_independent_explicit_owner_row(self):
        for data, count in ((self.closed, 2), (self.unknown, 1)):
            with self.subTest(scope=count):
                actual, result, report = self.consume(data, corrected=True)
                target = next(row for row in result['transactions'] if row['id'] == actual['id'])
                self.assertEqual(target, actual)
                self.assertEqual(len(result['transactions']), 2)
                fresh = next(row for row in result['transactions'] if row['id'] != actual['id'])
                self.assertEqual(fresh['owner'], 'Self')
                self.assertEqual(report['legacy_inheritance']['unbound_formal_ids'], [])
                self.assertEqual(len(report['owner_scope_view']['current_ids']), count)

    def test_unsupported_complete_predecessor_is_preserved_instead_of_owner_corrected(self):
        for field, value in (('instrument_type', 'Bond'),
                             ('position_effect_basis', 'unproved'),
                             ('unexpected_null', None)):
            with self.subTest(field=field):
                actual, result, report = self.consume(self.closed,
                    edit=lambda fact: fact.update({field: value}))
                self.assertEqual(next(row for row in result['transactions']
                                      if row['id'] == actual['id']), actual)
                self.assertEqual(len(result['transactions']), 2)
                self.assertEqual(report['legacy_inheritance']['field_corrections'], [])
                self.assertEqual(report['legacy_inheritance']['owner_scope_deferred_ids'], [actual['id']])
                self.assertNotIn(actual['id'], report['owner_scope_view']['current_ids'])
                self.assertEqual(report['owner_scope_decisions']['mechanism_ids'], [actual['id']])

    def test_actual_person_identity_is_required_but_snapshot_annotations_are_preserved(self):
        for field in ('display_name', 'role'):
            with self.subTest(field=field):
                actual, result, report = self.consume(self.closed,
                    edit_person=lambda person: person.update({field: 'Different official identity'}))
                self.assertEqual(next(row for row in result['transactions'] if row['id'] == actual['id']), actual)
                self.assertEqual(result['people'][0][field], 'Different official identity')
                self.assertEqual(report['legacy_inheritance']['field_corrections'], [])
                self.assertEqual(report['legacy_inheritance']['owner_scope_deferred_reasons'][actual['id']],
                                 'formal_person_identity_differs_from_source')
                self.assertNotIn(actual['id'], report['owner_scope_view']['current_ids'])
        annotations = {'portrait_url': 'https://example.invalid/portrait', 'priority': True,
                       'priority_reason': 'Existing snapshot note'}
        actual, result, report = self.consume(self.closed,
            edit_person=lambda person: person.update(annotations))
        self.assertEqual(next(row for row in result['transactions'] if row['id'] == actual['id']),
                         {**actual, 'owner': 'Undisclosed'})
        self.assertTrue(report['legacy_inheritance']['field_corrections'])
        self.assertTrue(all(result['people'][0][key] == value for key, value in annotations.items()))


if __name__ == '__main__':
    unittest.main()
