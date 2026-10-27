"""Normal candidate classification changes backed by generated native sources."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from test_oge_candidate import base as empty_base
from test_oge_owner_correction import fixture, write_files, CUTOFF, DOCUMENT
from unison_snapshot.codec import encode
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.whitehouse_native_correction import _historical_projection


def classification_candidate_inputs(root, *, owner=None, prior_owner=None, unknown=False,
                                    extra_row=False, asset='Example Common Stock See Endnote (EXM)'):
    """Return canonical sources and one existing source-derived historical fact.

    The partial formal input conserves an existing fact. An unknown source is
    never described as newly qualified by this test setup.
    """
    def mutate(raw):
        document = pymupdf.open(stream=raw, filetype='pdf')
        document[1].insert_text((30, 80), 'Additional information')
        result = document.tobytes(); document.close(); return result
    heading = {'Self':'Filer assets', 'Spouse':'Spouse assets',
               'Dependent Child':'Dependent children assets'}.get(owner, ' ')
    old, _, review, evidence, _ = fixture(asset=asset, heading=heading,
        pdf_mutator=mutate if unknown else None,
        extra_rows=[['', 'Filer assets', '', '', '', ''],
                    ['2', 'Independent Fund', 'Purchase', '06/02/2026', 'No', '$15,001 - $50,000']]
        if extra_row else None)
    root = Path(root); evidence_root, review_root = root/'evidence', root/'review'
    write_files(evidence_root, evidence); write_files(review_root, review)
    catalog = json.loads(next(raw for path, raw in evidence.items() if '/catalog/normalized/' in path))
    legacy = json.loads(next(raw for path, raw in review.items() if path.endswith('v2.json')))
    native = json.loads(next(raw for path, raw in review.items() if path.endswith('v7.json')))
    if owner in {'Spouse', 'Dependent Child'}:
        old = _historical_projection(native, native['transactions'][0], old['person_id'])
    if prior_owner is not None:
        old['owner'] = prior_owner
    base, _ = build_oge_candidate(catalog, [legacy], empty_base(), data_cutoff_at=CUTOFF,
                                  evidence_root=evidence_root)
    base['transactions'] = [deepcopy(old)]
    return {'catalog':catalog, 'legacy':legacy, 'native':native, 'base':base, 'prior':old,
            'evidence_root':evidence_root, 'review_root':review_root, 'document_id':DOCUMENT,
            'cutoff':CUTOFF}


class ClassificationCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-class-candidate-')
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)

    def run_candidate(self, data, *, generic=False, rooted=True, base=None, selected=None):
        source = deepcopy(selected if selected is not None else data['native'] if generic else data['legacy'])
        return build_oge_candidate(data['catalog'], [source], data['base'] if base is None else base,
            data_cutoff_at=data['cutoff'], evidence_root=data['evidence_root'] if rooted else None,
            source_extractions_by_document={data['document_id']:source},
            **({'legacy_extractions_by_document':{data['document_id']:data['legacy']}} if generic else {}))

    def test_default_and_generic_consume_class_only_and_approved_owner_combination(self):
        for owner in (None, 'Self', 'Spouse', 'Dependent Child'):
            data = classification_candidate_inputs(self.root/str(owner), owner=owner)
            for generic in (False, True):
                with self.subTest(owner=owner, generic=generic):
                    candidate, audit = self.run_candidate(data, generic=generic)
                    expected = {**data['prior'], 'instrument_type':'Stock',
                        'owner':'Undisclosed' if owner is None and generic else data['prior']['owner']}
                    self.assertEqual(candidate['transactions'], [expected])
                    receipt = audit['reports'][0]['native_classification']
                    self.assertEqual(receipt['qualified_ids'], [expected['id']])
                    self.assertEqual(receipt['current_ids'], [expected['id']])
                    self.assertEqual(audit['current_qualified_transaction_count'], 1)
                    application = receipt['applications'][0]
                    self.assertEqual(application['old_fact_sha256'], hashlib.sha256(encode(data['prior'])).hexdigest())
                    self.assertEqual(application['new_fact_sha256'], hashlib.sha256(encode(expected)).hexdigest())
                    self.assertNotIn('deferred_cross_source_formal_ids', audit['reports'][0])
                    self.assertEqual(audit['reports'][0]['promoted_count'], 1)
                    if owner is None and generic:
                        correction = audit['reports'][0]['legacy_inheritance']['field_corrections']
                        self.assertEqual(len(correction), 1)
                        self.assertEqual(correction[0]['rule'], 'approved_v2_default_self_to_undisclosed/v1')
                    else:
                        self.assertEqual(application['changed_fields'], ['instrument_type'])

    def test_existing_undisclosed_conservation_nullable_shape_annotations_and_real_repeat(self):
        data = classification_candidate_inputs(self.root, prior_owner='Undisclosed')
        prior = data['base']['transactions'][0]
        for field in ['option_type','strike_price','expiration_date','position_effect_basis']:
            prior.pop(field)
        annotations = {'portrait_url':'https://example.invalid/existing', 'priority':True,
                       'priority_reason':'Existing source annotation'}
        data['base']['people'][0].update(annotations)
        for generic in (False, True):
            with self.subTest(generic=generic):
                candidate, audit = self.run_candidate(data, generic=generic)
                self.assertEqual(candidate['transactions'], [{**prior,'instrument_type':'Stock'}])
                self.assertEqual(set(candidate['transactions'][0]), set(prior))
                self.assertEqual(candidate['people'], data['base']['people'])
                report = audit['reports'][0]
                self.assertEqual(report['native_classification']['current_ids'], [prior['id']])
                if not generic:
                    self.assertEqual(report['default_owner_conservation']['current_ids'], [prior['id']])
                repeat, repeated_audit = self.run_candidate(data, generic=generic, base=candidate)
                self.assertEqual(repeat['transactions'], candidate['transactions'])
                self.assertEqual(repeated_audit['reports'][0]['native_classification']['applications'], [])
                self.assertEqual(repeated_audit['current_qualified_transaction_count'], 1)

    def test_unknown_rootless_and_wrong_full_predecessor_defer_without_blocking_independent_row(self):
        for case in ('unknown','rootless','wrong_fact','rootless_spouse'):
            data = classification_candidate_inputs(self.root/case, unknown=case=='unknown',
                owner='Spouse' if case == 'rootless_spouse' else None, extra_row=True)
            if case == 'wrong_fact':
                data['base']['transactions'][0]['position_effect_basis'] = 'unproved'
            prior = deepcopy(data['base']['transactions'][0])
            for generic in (False, True):
                with self.subTest(case=case, generic=generic):
                    candidate, audit = self.run_candidate(data, generic=generic,
                        rooted=case not in {'rootless','rootless_spouse'})
                    self.assertEqual(next(row for row in candidate['transactions'] if row['id']==prior['id']), prior)
                    self.assertEqual(len(candidate['transactions']), 2)
                    fresh = next(row for row in candidate['transactions'] if row['id']!=prior['id'])
                    self.assertEqual(fresh['owner'], 'Self')
                    receipt = audit['reports'][0]['native_classification']
                    self.assertEqual(receipt['applications'], [])
                    self.assertEqual(receipt['current_ids'], [])
                    self.assertEqual(receipt['qualified_ids'], [fresh['id']])
                    self.assertEqual(audit['current_qualified_transaction_count'], 1)

    def test_people_identity_and_financial_blockers_prevent_all_partial_mutation(self):
        data = classification_candidate_inputs(self.root)
        wrong_person = deepcopy(data['base']); wrong_person['people'][0]['role']='Different official role'
        candidate, audit = self.run_candidate(data, generic=True, base=wrong_person)
        self.assertEqual(candidate['transactions'], wrong_person['transactions'])
        self.assertEqual(candidate['people'], wrong_person['people'])
        self.assertEqual(audit['reports'][0]['legacy_inheritance']['field_corrections'], [])
        self.assertEqual(audit['current_qualified_transaction_count'], 0)
        selected = deepcopy(data['legacy']); selected['document_reasons']=['filer_signature_date_not_unique']
        selected['evidence_complete']=False
        candidate, audit = self.run_candidate(data, rooted=False, selected=selected)
        self.assertEqual(candidate['transactions'], data['base']['transactions'])
        self.assertEqual(audit['current_qualified_transaction_count'], 0)

    def test_ambiguous_old_bindings_do_not_create_a_default_self_copy(self):
        data = classification_candidate_inputs(self.root, owner='Spouse', extra_row=True)
        old_self = _historical_projection(data['legacy'], data['legacy']['transactions'][0], data['prior']['person_id'])
        data['base']['transactions'].append(old_self)
        for generic in (False, True):
            with self.subTest(generic=generic):
                candidate, audit = self.run_candidate(data, generic=generic)
                self.assertEqual(len(candidate['transactions']), 3)
                for prior in data['base']['transactions']:
                    self.assertEqual(next(fact for fact in candidate['transactions'] if fact['id']==prior['id']), prior)
                self.assertEqual(audit['reports'][0]['native_classification']['current_ids'], [])
                self.assertEqual(audit['current_qualified_transaction_count'], 1)

    def test_already_stock_keeps_existing_owner_only_and_unchanged_current_contracts(self):
        data = classification_candidate_inputs(self.root/'implicit')
        data['base']['transactions'][0]['instrument_type'] = 'Stock'
        candidate, audit = self.run_candidate(data, generic=True)
        self.assertEqual(candidate['transactions'], [{**data['base']['transactions'][0], 'owner':'Undisclosed'}])
        self.assertEqual(audit['reports'][0]['native_classification']['applications'], [])
        self.assertEqual(len(audit['reports'][0]['legacy_inheritance']['field_corrections']), 1)
        self.assertEqual(audit['current_qualified_transaction_count'], 1)
        explicit = classification_candidate_inputs(self.root/'explicit', owner='Self')
        explicit['base']['transactions'][0]['instrument_type'] = 'Stock'
        for generic in (False, True):
            candidate, audit = self.run_candidate(explicit, generic=generic, rooted=False)
            self.assertEqual(candidate['transactions'], explicit['base']['transactions'])
            self.assertEqual(audit['current_qualified_transaction_count'], 1)

    def test_genuine_bond_endnote_and_fresh_endnote_do_not_gain_a_global_classification_gate(self):
        bond = classification_candidate_inputs(self.root/'bond', owner='Self',
            asset='Treasury Bond See Endnote (BND)')
        candidate, audit = self.run_candidate(bond)
        self.assertEqual(candidate['transactions'], bond['base']['transactions'])
        self.assertNotIn('native_classification', audit['reports'][0])
        old, _, review, evidence, base = fixture(asset='Old Common Stock (OLD)', heading='Filer assets',
            extra_rows=[['2', 'New Stock See Endnote (NEW)', 'Purchase', '06/02/2026', 'No', '$15,001-$50,000']])
        root = self.root/'fresh'; write_files(root, evidence)
        catalog = json.loads(next(raw for path, raw in evidence.items() if '/catalog/normalized/' in path))
        legacy = json.loads(next(raw for path, raw in review.items() if path.endswith('v2.json')))
        with patch('unison_snapshot.oge_native_classification.NativeClassification._authenticate',
                   side_effect=AssertionError('A fresh row is not an old classification migration')):
            candidate, audit = build_oge_candidate(catalog, [legacy], base,
                data_cutoff_at=CUTOFF, evidence_root=root)
        self.assertEqual(len(candidate['transactions']), 2)
        self.assertEqual({fact['instrument_type'] for fact in candidate['transactions']}, {'Stock'})
        self.assertNotIn('native_classification', audit['reports'][0])


if __name__=='__main__':
    unittest.main()
