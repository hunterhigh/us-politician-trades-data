"""Default execution conserves existing owner facts without selecting generic.

Source PDFs are generated contracts. A partial formal board is an input for
preservation tests, never a claim that an unknown source received new approval.
"""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from test_oge_owner_correction import fixture, write_files, DOCUMENT, CUTOFF
from test_oge_scoped_generic_cli import make_mixed_inputs
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge import OgeCatalogError


def _unknown_text(raw):
    pdf = pymupdf.open(stream=raw, filetype='pdf')
    pdf[1].insert_text((30, 80), 'Additional information')
    result = pdf.tobytes(); pdf.close()
    return result


@lru_cache(maxsize=4)
def _source(unknown, new_row):
    return fixture(pdf_mutator=_unknown_text if unknown else None,
        extra_rows=[['2', 'Independent Fund', 'Purchase', '06/02/2026', 'No', '$15,001 - $50,000']]
        if new_row else None)


def make_default_owner_inputs(root, *, unknown=False, new_row=True):
    """Write a canonical source and an existing partial Howner formal board.

    Returns evidence_root/review_root, catalog/legacy/native, prior, target,
    initial_base (Self), document_id, cutoff. Unknown is preserved, not approved.
    """
    root = Path(root)
    old, target, review, evidence, initial = deepcopy(_source(unknown, new_row))
    evidence_root, review_root = root/'evidence', root/'review'
    write_files(evidence_root, evidence); write_files(review_root, review)
    prior = deepcopy(initial); prior['transactions'] = [deepcopy(target)]
    catalog = json.loads(next(raw for path, raw in evidence.items() if '/catalog/normalized/' in path))
    legacy = json.loads(next(raw for path, raw in review.items() if path.endswith('pdf-v2.json')))
    native = json.loads(next(raw for path, raw in review.items() if path.endswith('pdf-v7.json')))
    return {'evidence_root': evidence_root, 'review_root': review_root, 'catalog': catalog,
        'legacy': legacy, 'native': native, 'prior': prior, 'target': target,
        'initial_base': initial, 'document_id': DOCUMENT, 'cutoff': CUTOFF}


class DefaultOwnerCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-default-candidate-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def build(self, data, *, roots=True, raw=None, base=None):
        selected = deepcopy(data['legacy'] if raw is None else raw)
        return build_oge_candidate(data['catalog'], [selected], data['prior'] if base is None else base,
            data_cutoff_at=data['cutoff'], evidence_root=data['evidence_root'] if roots else None,
            source_extractions_by_document={data['document_id']: selected})

    def test_real_mixed_output_as_next_base_keeps_owner_and_default_raw_selection(self):
        data = make_mixed_inputs(self.root)
        catalog = json.loads(data['catalog_path'].read_bytes())
        original = json.loads(data['base_path'].read_bytes())
        source = data['extractions_root']
        a2 = json.loads((source/data['a']/data['sha_a']/'oge-278t-pdf-v2.json').read_bytes())
        a7 = json.loads((source/data['a']/data['sha_a']/'oge-278t-pdf-v7.json').read_bytes())
        b2 = json.loads((source/data['b']/data['sha_b']/'oge-278t-pdf-v2.json').read_bytes())
        actual_output, _ = build_oge_candidate(catalog, [a7, b2], original,
            data_cutoff_at=data['cutoff'], evidence_root=data['evidence_root'],
            legacy_extractions_by_document={data['a']: a2})
        conserved, audit = build_oge_candidate(catalog, [a2, b2], actual_output,
            data_cutoff_at=data['cutoff'], evidence_root=data['evidence_root'])
        self.assertEqual(conserved['transactions'], actual_output['transactions'])
        self.assertEqual(next(row for row in conserved['transactions'] if row['filing_id'] == data['b']), data['old_b'])
        a_report = next(row for row in audit['reports'] if row['document_id'] == data['a'])
        b_report = next(row for row in audit['reports'] if row['document_id'] == data['b'])
        self.assertEqual(a_report['source_interpretation_binding']['parser_version'], 'oge-278t-pdf/v2')
        self.assertEqual(a_report['default_owner_conservation']['current_ids'], [data['old_a']['id']])
        self.assertNotIn('default_owner_conservation', b_report)
        self.assertEqual(audit['current_qualified_transaction_count'], 2)

    def test_closed_unknown_and_no_root_keep_complete_old_object_and_default_new_self(self):
        for unknown, roots, count in ((False, True, 2), (True, True, 1), (False, False, 1)):
            with self.subTest(unknown=unknown, roots=roots):
                data = make_default_owner_inputs(self.root/f'{unknown}-{roots}', unknown=unknown)
                prior = deepcopy(data['prior'])
                candidate, audit = self.build(data, roots=roots)
                self.assertEqual(next(row for row in candidate['transactions'] if row['id'] == data['target']['id']), data['target'])
                fresh = next(row for row in candidate['transactions'] if row['id'] != data['target']['id'])
                self.assertEqual(fresh['owner'], 'Self')
                self.assertEqual(audit['current_qualified_transaction_count'], count)
                report = audit['reports'][0]; receipt = report['default_owner_conservation']
                self.assertEqual(receipt['retained_ids'], [data['target']['id']])
                self.assertEqual(receipt['current_ids'], [data['target']['id']] if count == 2 else [])
                self.assertEqual(data['prior'], prior)
                self.assertEqual(report['source_interpretation_binding']['parser_version'], 'oge-278t-pdf/v2')

    def test_bad_row_and_document_blockers_preserve_old_without_current_credit(self):
        for whole_document in (False, True):
            with self.subTest(document=whole_document):
                data = make_default_owner_inputs(self.root/str(whole_document))
                raw = deepcopy(data['legacy'])
                if whole_document:
                    raw['document_reasons'] = ['filer_signature_date_not_unique']
                    raw['evidence_complete'] = False
                else:
                    target = raw['transactions'].pop(0)
                    raw['quarantined'].append({**target, 'reasons': ['amount_range_unreadable']})
                candidate, audit = self.build(data, roots=False, raw=raw)
                self.assertEqual(next(row for row in candidate['transactions'] if row['id'] == data['target']['id']), data['target'])
                receipt = audit['reports'][0]['default_owner_conservation']
                self.assertEqual(receipt['current_ids'], [])
                self.assertEqual(audit['current_qualified_transaction_count'], 0 if whole_document else 1)

    def test_complete_alias_mismatch_is_kept_without_requalifying_and_auth_error_is_fatal(self):
        data = make_default_owner_inputs(self.root)
        prior = deepcopy(data['prior']); prior['transactions'][0]['instrument_type'] = 'Bond'
        candidate, audit = self.build(data, base=prior)
        self.assertEqual(next(row for row in candidate['transactions'] if row['id'] == data['target']['id']), prior['transactions'][0])
        self.assertEqual(audit['reports'][0]['default_owner_conservation']['current_ids'], [])
        self.assertEqual(audit['current_qualified_transaction_count'], 1)
        tampered = deepcopy(data['legacy']); tampered['owner_scope_complete'] = True
        with self.assertRaises(OgeCatalogError):
            self.build(data, raw=tampered)

    def test_default_without_a_protected_prior_does_not_read_auxiliary_or_add_a_receipt(self):
        data = make_default_owner_inputs(self.root)
        with patch('unison_snapshot.oge_default_owner_conservation.prepare_default_owner_conservation',
                   side_effect=AssertionError('No conservation request')):
            candidate, audit = self.build(data, base=data['initial_base'])
        self.assertEqual({row['owner'] for row in candidate['transactions']}, {'Self'})
        self.assertNotIn('default_owner_conservation', audit['reports'][0])
        self.assertNotIn('current_qualified_transaction_count', audit)


if __name__ == '__main__':
    unittest.main()
