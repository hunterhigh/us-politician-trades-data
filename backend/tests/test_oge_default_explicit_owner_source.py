"""Same-layout source contracts for conserving already published native owners."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from test_oge_candidate import base as empty_base
from test_oge_owner_correction import fixture, write_files, CUTOFF
from unison_snapshot.codec import encode
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_default_owner_conservation import prepare_default_owner_conservation
from unison_snapshot.whitehouse_native_correction import _historical_projection


@lru_cache(maxsize=6)
def source_case(owner='Spouse', *, unknown=False, endnote=False):
    def mutate(raw):
        pdf = pymupdf.open(stream=raw, filetype='pdf')
        pdf[1].insert_text((30, 80), 'Additional information')
        result = pdf.tobytes(); pdf.close(); return result
    heading = 'Spouse assets' if owner == 'Spouse' else 'Dependent children assets'
    asset = 'Example Common Stock See Endnote (EXM)' if endnote else 'Example Common Stock (EXM)'
    _, _, review, evidence, _ = fixture(asset=asset, heading=heading,
        historical=False, pdf_mutator=mutate if unknown else None)
    catalog = json.loads(next(value for path,value in evidence.items() if '/catalog/normalized/' in path))
    legacy = json.loads(next(value for path,value in review.items() if path.endswith('v2.json')))
    native = json.loads(next(value for path,value in review.items() if path.endswith('v7.json')))
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); write_files(root,evidence)
        candidate, _ = build_oge_candidate(catalog,[native],empty_base(),
            data_cutoff_at=CUTOFF,evidence_root=root,
            legacy_extractions_by_document={legacy['document_id']:legacy})
    if len(candidate['transactions']) != 1 or candidate['transactions'][0]['owner'] != owner:
        raise AssertionError('The base must be an actual fresh generic output')
    return legacy, native, catalog['transactions'][0], evidence, candidate['transactions'][0]


class DefaultExplicitOwnerSourceTests(unittest.TestCase):
    def context(self, data, *, facts=None, raw=None, evidence_root=True):
        legacy, _, record, evidence, prior = deepcopy(data)
        if raw is not None:
            legacy = raw
        temporary = tempfile.TemporaryDirectory(prefix='oge-explicit-source-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name); write_files(root,evidence)
        return prepare_default_owner_conservation(legacy,[prior] if facts is None else facts,record,
            source_extraction=legacy,evidence_root=root if evidence_root else None)

    def test_actual_generic_spouse_and_child_bind_to_default_row_without_changing_raw_or_id(self):
        for owner in ('Spouse','Dependent Child'):
            with self.subTest(owner=owner):
                data = source_case(owner)
                legacy, native, _, _, prior = deepcopy(data)
                before = deepcopy((legacy,native,prior))
                context = self.context(data)
                self.assertEqual(context.status,'observed')
                self.assertEqual(context.protected_ids,{prior['id']})
                self.assertEqual(context.source_current_ids([prior]),{prior['id']})
                self.assertEqual(context.blocked_selected_ids,set())
                selected = context.selected_binding(prior)
                self.assertEqual(selected,legacy['transactions'][0])
                self.assertNotEqual(selected['extraction_id'],prior['id'])
                self.assertEqual(selected['owner'],'Self')
                self.assertEqual(context.binding['source']['parser_version'],'oge-278t-pdf/v2')
                self.assertEqual(context.binding['source']['extraction_canonical_sha256'],
                                 hashlib.sha256(encode(legacy)).hexdigest())
                self.assertEqual(context.binding['auxiliary_owner_scope']['legacy'],context.binding['source'])
                selected['owner'] = 'FORGED WORKING COPY'
                self.assertEqual(context.selected_binding(prior)['owner'],'Self')
                self.assertEqual((legacy,native,prior),before)

    def test_known_nullable_omission_stays_current_without_changing_the_existing_object(self):
        data = source_case()
        prior = {key:value for key,value in data[4].items() if value is not None}
        context = self.context(data,facts=[prior])
        self.assertEqual(context.source_current_ids([prior]),{prior['id']})
        self.assertEqual(set(prior),{key for key,value in data[4].items() if value is not None})

    def test_wrong_full_old_objects_still_bind_for_conservation_but_are_not_current(self):
        data = source_case()
        for field,value in (('amount_low',1002),('option_type','call'),('unexpected_null',None)):
            with self.subTest(field=field):
                prior = {**data[4],field:value}
                context = self.context(data,facts=[prior])
                self.assertEqual(context.protected_ids,{prior['id']})
                self.assertIsNotNone(context.selected_binding(prior))
                self.assertEqual(context.source_current_ids([prior]),set())
                # A later source-looking financial rewrite is not authority.
                self.assertEqual(context.source_current_ids([data[4]]),set())

    def test_unknown_scope_binds_only_the_related_row_and_keeps_current_unknown(self):
        data = source_case(unknown=True)
        context = self.context(data)
        self.assertEqual(context.status,'unknown')
        self.assertEqual(context.protected_ids,{data[4]['id']})
        self.assertEqual(context.selected_binding(data[4]),data[0]['transactions'][0])
        self.assertEqual(context.source_current_ids([data[4]]),set())
        self.assertEqual(context.blocked_selected_ids,set())

    def test_missing_root_and_wrong_whole_cache_fail_before_any_alias_is_used(self):
        data = source_case()
        with self.assertRaisesRegex(OgeCatalogError,'requires the original source root'):
            self.context(data,evidence_root=False)
        altered = {**data[0],'explicit_owner_source_complete':True}
        with self.assertRaisesRegex(OgeCatalogError,'whole-cache replay differs'):
            self.context(data,raw=altered)
        wrong_url = {**data[4],'source_url':'https://example.invalid/wrong.pdf'}
        with self.assertRaisesRegex(OgeCatalogError,'formal source URL differs'):
            self.context(data,facts=[wrong_url])

    def test_two_old_ids_for_one_source_row_are_preserved_without_choosing_a_winner(self):
        data = source_case()
        legacy,native,_,_,prior = deepcopy(data)
        alias = _historical_projection(legacy,legacy['transactions'][0],prior['person_id'])
        alias['owner'] = 'Undisclosed'
        context = self.context(data,facts=[prior,alias])
        self.assertEqual(context.protected_ids,{prior['id'],alias['id']})
        self.assertEqual(context.blocked_selected_ids,{legacy['transactions'][0]['extraction_id']})
        self.assertIsNone(context.selected_binding(prior))
        self.assertIsNone(context.selected_binding(alias))
        self.assertEqual(context.source_current_ids([prior,alias]),set())

    def test_fresh_input_without_an_existing_target_does_not_add_an_auxiliary_source_gate(self):
        legacy,_,record,_,prior = deepcopy(source_case())
        with patch('unison_snapshot.oge_reports.parse_archived_pdf',side_effect=AssertionError('No auxiliary read')):
            context = prepare_default_owner_conservation(legacy,[],record,evidence_root=Path('unused'))
        self.assertEqual(context.protected_ids,set())
        self.assertEqual(context.blocked_selected_ids,set())

    def test_actual_final_endnote_stock_can_be_rechecked_without_authorizing_a_classification_change(self):
        data = source_case(endnote=True)
        legacy,native,_,_,final = deepcopy(data)
        prior = _historical_projection(native,native['transactions'][0],final['person_id'])
        self.assertEqual(prior['instrument_type'],'Bond')
        context = self.context(data,facts=[prior])
        self.assertEqual(context.source_current_ids([prior]),set())
        self.assertEqual(context.source_current_ids([final]),{prior['id']})
        self.assertEqual(final,{**prior,'instrument_type':'Stock'})
        self.assertEqual(context.selected_binding(prior),legacy['transactions'][0])


if __name__=='__main__':
    unittest.main()
