"""Finite source and projection contracts; generated PDFs do not measure OCR."""
from copy import deepcopy
from functools import lru_cache
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
from unison_snapshot.oge_native_classification import prepare_native_classification
from unison_snapshot.whitehouse_native_correction import _historical_projection


@lru_cache(maxsize=8)
def source_case(owner=None, unknown=False, asset='Example Common Stock See Endnote (EXM)'):
    heading = {'Self':'Filer assets', 'Spouse':'Spouse assets',
               'Dependent Child':'Dependent children assets'}.get(owner, ' ')
    def mutate(raw):
        document = pymupdf.open(stream=raw, filetype='pdf')
        document[1].insert_text((30, 80), 'Additional information')
        result = document.tobytes(); document.close(); return result
    old, _, review, evidence, _ = fixture(asset=asset,
        heading=heading, pdf_mutator=mutate if unknown else None)
    legacy = json.loads(next(value for path, value in review.items() if path.endswith('v2.json')))
    native = json.loads(next(value for path, value in review.items() if path.endswith('v7.json')))
    record = json.loads(next(value for path, value in evidence.items()
                             if '/catalog/normalized/' in path))['transactions'][0]
    if owner in {'Spouse', 'Dependent Child'}:
        old = _historical_projection(native, native['transactions'][0], old['person_id'])
    return old, legacy, native, record, evidence


class OgeNativeClassificationTests(unittest.TestCase):
    def context(self, data, prior, *, generic=False, rooted=True, edit_source=None):
        old, legacy, native, record, evidence = deepcopy(data)
        selected = native if generic else legacy
        if edit_source:
            edit_source(selected)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        write_files(root, evidence)
        return prepare_native_classification(selected, [prior], record,
            evidence_root=root if rooted else None, source_extraction=selected,
            legacy_extraction=legacy if generic else None)

    def test_both_selected_readers_cover_default_corrected_and_all_explicit_owners(self):
        for owner in (None, 'Self', 'Spouse', 'Dependent Child'):
            data = source_case(owner)
            for prior_owner in (('Self','Undisclosed') if owner is None else (owner,)):
                for generic in (False, True):
                    with self.subTest(owner=owner, old_owner=prior_owner, generic=generic):
                        prior = {**data[0], 'owner':prior_owner}
                        context = self.context(data, prior, generic=generic)
                        expected_owner = 'Undisclosed' if owner is None and generic else prior_owner
                        expected = {**prior, 'instrument_type':'Stock', 'owner':expected_owner}
                        decision = context.decision(prior)
                        self.assertEqual(context.status, 'observed')
                        self.assertEqual(context.mechanism_ids, {prior['id']})
                        self.assertEqual(decision['status'], 'approved')
                        self.assertEqual(decision['fact'], expected)
                        self.assertEqual(context.reconcile(prior)['fact'], expected)
                        self.assertEqual(context.source_current_ids([expected]), {prior['id']})
                        self.assertEqual(context.legacy_binding(prior)['extraction_id'], prior['id'])
                        selected = context.selected_binding(prior)
                        self.assertEqual(selected['cells'], data[1]['transactions'][0]['cells'])
                        if owner in {'Spouse','Dependent Child'} and not generic:
                            self.assertNotEqual(selected['extraction_id'], prior['id'])
                        self.assertEqual(context.binding['source']['parser_version'],
                            'oge-278t-pdf/v7' if generic else 'oge-278t-pdf/v2')

    def test_nullable_omission_and_actual_stock_repeat_keep_the_same_representation(self):
        data = source_case()
        prior = {key:value for key,value in data[0].items() if value is not None}
        context = self.context(data, prior, generic=True)
        final = context.reconcile(prior)['fact']
        self.assertEqual(set(final), set(prior))
        repeat = self.context(data, final, generic=True)
        self.assertEqual(repeat.binding, context.binding)
        self.assertEqual(repeat.decision(final)['status'], 'current')
        self.assertIsNone(repeat.reconcile(final))
        self.assertEqual(repeat.source_current_ids([final]), {final['id']})
        self.assertEqual(set(repeat.decision(final)['fact']), set(prior))

    def test_full_old_mismatch_is_bound_for_conservation_but_never_migrated(self):
        data = source_case('Self')
        for field, value in (('amount_low',1002), ('option_type','call'),
                             ('unexpected_null',None), ('instrument_type','Unspecified')):
            with self.subTest(field=field):
                prior = {**data[0], field:value}
                context = self.context(data, prior, generic=True)
                self.assertEqual(context.mechanism_ids, {prior['id']})
                self.assertEqual(context.selected_binding(prior)['extraction_id'], prior['id'])
                self.assertIsNotNone(context.legacy_binding(prior))
                self.assertEqual(context.decision(prior)['status'], 'deferred')
                self.assertIsNone(context.reconcile(prior))
                self.assertEqual(context.source_current_ids([prior]), set())

    def test_rootless_and_unknown_preserve_without_source_positive_and_block_unbound_duplicates(self):
        for data, rooted in ((source_case(), False), (source_case(unknown=True), True)):
            prior = deepcopy(data[0])
            context = self.context(data, prior, generic=True, rooted=rooted)
            self.assertEqual(context.status, 'unknown')
            self.assertIsNone(context.reconcile(prior))
            self.assertEqual(context.source_current_ids([prior]), set())
            self.assertEqual(context.unchanged_current_ids([prior]), set())
        data = source_case('Spouse')
        context = self.context(data, data[0], rooted=False)
        self.assertEqual(context.mechanism_ids, {data[0]['id']})
        self.assertIsNone(context.selected_binding(data[0]))
        self.assertEqual(context.blocked_selected_ids, {data[1]['transactions'][0]['extraction_id']})

    def test_exact_fresh_current_compatibility_remains_separate_from_source_proof(self):
        data = source_case('Self')
        prior = {**data[0], 'instrument_type':'Stock'}
        for generic in (False, True):
            context = self.context(data, prior, generic=generic, rooted=False)
            self.assertEqual(context.source_current_ids([prior]), set())
            self.assertEqual(context.unchanged_current_ids([prior]), {prior['id']})
            self.assertIsNone(context.reconcile(prior))
        altered = {**prior, 'unexpected_null':None}
        context = self.context(data, altered, rooted=False)
        self.assertEqual(context.unchanged_current_ids([altered]), set())
        # A Stock predecessor still needing owner migration is not unchanged.
        implicit = source_case()
        prior = {**implicit[0], 'instrument_type':'Stock'}
        context = self.context(implicit, prior, generic=True, rooted=False)
        self.assertEqual(context.unchanged_current_ids([prior]), set())

    def test_no_target_skips_original_and_wrong_whole_raw_cache_is_strict(self):
        old, legacy, native, record, _ = deepcopy(source_case('Self'))
        with patch('unison_snapshot.oge_reports.parse_archived_pdf', side_effect=AssertionError('No read')):
            context = prepare_native_classification(legacy, [], record,
                evidence_root=Path('missing-root'))
            self.assertEqual(context.mechanism_ids, set())
        with self.assertRaisesRegex(OgeCatalogError, 'whole-cache replay differs'):
            self.context(source_case('Self'), old,
                edit_source=lambda source: source.update(owner_scope_complete=True))
        changed = deepcopy(legacy)
        changed.update(filed_at=None, evidence_complete=False)
        effective = deepcopy(legacy)
        context = prepare_native_classification(effective, [old], record, source_extraction=changed)
        self.assertEqual(context.source_current_ids([old]), set())
        self.assertEqual(context.unchanged_current_ids([old]), set())
        self.assertEqual(context.binding['source']['extraction_canonical_sha256'],
                         hashlib.sha256(encode(changed)).hexdigest())

    def test_two_existing_ids_for_one_physical_row_do_not_choose_a_winner(self):
        data = source_case('Spouse')
        prior, legacy, native, record, evidence = deepcopy(data)
        legacy_prior = _historical_projection(legacy, legacy['transactions'][0], prior['person_id'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); write_files(root,evidence)
            context = prepare_native_classification(native, [prior,legacy_prior], record,
                evidence_root=root, legacy_extraction=legacy)
        self.assertEqual(context.mechanism_ids, {prior['id'], legacy_prior['id']})
        self.assertIsNone(context.selected_binding(prior))
        self.assertIsNone(context.selected_binding(legacy_prior))
        self.assertEqual(context.blocked_selected_ids, {native['transactions'][0]['extraction_id']})
        self.assertEqual(context.source_current_ids([prior,legacy_prior]), set())

    def test_explained_non_stock_endnote_rows_do_not_enter_classification_scope(self):
        for asset in ('Example Treasury Bond See Endnote (EXM)',
                      'Example ETF See Endnote (EXM)', 'Unspecified See Endnote'):
            with self.subTest(asset=asset):
                data = source_case(asset=asset)
                context = self.context(data, data[0], generic=True)
                self.assertEqual(context.mechanism_ids, set())
                self.assertEqual(context.blocked_selected_ids, set())
                self.assertIsNone(context.reconcile(data[0]))

    def test_stock_with_existing_owner_only_policy_is_not_reclassified_or_cancelled(self):
        data = source_case()
        prior = {**data[0], 'instrument_type':'Stock'}
        context = self.context(data, prior, generic=True)
        expected = {**prior, 'owner':'Undisclosed'}
        decision = context.decision(prior)
        self.assertEqual(decision['status'], 'current')
        self.assertEqual(decision['fact'], expected)
        self.assertEqual(decision['changed_fields'], ['owner'])
        self.assertEqual(decision['corrections'][0]['rule'], 'approved_v2_default_self_to_undisclosed/v1')
        self.assertIsNone(context.reconcile(prior))
        self.assertEqual(context.source_current_ids([expected]), {prior['id']})

    def test_unchanged_manual_effective_date_keeps_raw_unknown_and_ordinary_current_qualification(self):
        from unison_snapshot.oge_manual_dates import apply_manual_cover_dates
        prior, original, _, record, _ = deepcopy(source_case('Self'))
        prior['instrument_type'] = 'Stock'
        raw = {**original, 'filed_at':None, 'evidence_complete':False,
               'document_reasons':['filer_signature_date_not_unique']}
        observed = original['filed_at']
        observation = {'schema_version':'oge-manual-cover-date/v1', 'status':'observation_only',
            'qualification_effect':'none', 'document_id':raw['document_id'],
            'source_sha256':raw['source_sha256'], 'source_url':raw['source_url'],
            'extraction_parser_version':'oge-278t-pdf/v2', 'page_number':1,
            'recognition_method':'human_visual_reading_of_pdf_cover',
            'field_role':'filer_signature_date', 'raw_reading':'6/13/2026',
            'observed_date':observed, 'filed_at':observed}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/raw['document_id']/(raw['source_sha256']+'.json')
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(observation),encoding='utf-8')
            effective = apply_manual_cover_dates([raw],root)[0]
        context = prepare_native_classification(effective,[prior],record,source_extraction=raw)
        self.assertEqual(context.source_current_ids([prior]),set())
        self.assertEqual(context.unchanged_current_ids([prior]),{prior['id']})
        self.assertIsNone(raw['filed_at'])
        self.assertEqual(context.binding['source']['extraction_canonical_sha256'],
                         hashlib.sha256(encode(raw)).hexdigest())
        without_layer = deepcopy(effective)
        without_layer.pop('manual_filing_date_evidence')
        unbound = prepare_native_classification(without_layer,[prior],record,source_extraction=raw)
        self.assertEqual(unbound.unchanged_current_ids([prior]),set())

    def test_fresh_endnote_row_does_not_make_an_unrelated_old_fact_a_source_target(self):
        old, _, review, evidence, _ = fixture(asset='Old Common Stock (OLD)', heading='Filer assets',
            extra_rows=[['2', 'New Common Stock See Endnote (NEW)', 'Purchase',
                         '06/02/2026', 'No', '$15,001 - $50,000']])
        record = json.loads(next(value for path,value in evidence.items()
                                 if '/catalog/normalized/' in path))['transactions'][0]
        legacy = json.loads(next(value for path,value in review.items() if path.endswith('v2.json')))
        native = json.loads(next(value for path,value in review.items() if path.endswith('v7.json')))
        self.assertEqual(old['instrument_type'],'Stock')
        for source in (legacy,native):
            with self.subTest(parser=source['parser_version']), patch(
                    'unison_snapshot.oge_native_classification.NativeClassification._authenticate',
                    side_effect=AssertionError('No old classification target requires source replay')):
                context = prepare_native_classification(source,[old],record,
                    evidence_root=Path('unused-root'),legacy_extraction=legacy)
                self.assertEqual(context.mechanism_ids,set())
                self.assertEqual(context.status,'not_applicable')


if __name__ == '__main__':
    unittest.main()
