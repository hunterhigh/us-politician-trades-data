from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_house_ptr import META, IDENTITY, fixture_pages
from unison_snapshot.house_ptr import parse_word_pages, qualify_automatic, LEGACY_PARSER_VERSION
from unison_snapshot.house_open_amounts import threshold, quarantined_limitations, refresh_qualifications, refresh_candidate
from unison_snapshot.publication_amounts import project


def legacy_open():
    value = parse_word_pages(META, 'b' * 64, fixture_pages(), copy_allowed=True)
    value.pop('source_word_pages'); value.pop('source_word_pages_sha256')
    value['parser_version'] = 'house-ptr-2026-04'
    value['transactions'][0].update(amount_low=1000001, amount_high=None,
        amount_kind='open_ended', amount_raw='Spouse/DC Over $1,000,000')
    value['transactions'][0].pop('amount_low_exclusive', None)
    return value


class HouseOpenAmountTests(unittest.TestCase):
    def test_legacy_threshold_reaches_option_c_without_changing_finite_facts(self):
        extraction = legacy_open(); original = deepcopy(extraction)
        qualified = qualify_automatic(extraction, IDENTITY)
        opened = qualified['transactions'][0]
        self.assertEqual((opened['amount_low'], opened['amount_high'], opened['amount_low_exclusive']),
                         (1000000, None, True))
        finite = qualify_automatic(parse_word_pages(META, 'a' * 64, fixture_pages(),
            copy_allowed=True), IDENTITY)['transactions']
        payload = {'meta': {}, 'people': [{'id': IDENTITY['person_id'], 'display_name': 'Example'}],
                   'transactions': finite + [opened], 'reported_holdings': []}
        result = project(payload)
        self.assertEqual(result['transactions'], finite)
        receipt = result['meta']['known_gaps']['excluded_open_amounts']['transactions'][0]
        self.assertEqual((receipt['threshold'], receipt['record_ids']),
                         ('Over $1,000,000', [opened['id']]))
        self.assertEqual(extraction, original)

    def test_paper_keeps_other_blockers_and_exposes_only_observed_open_threshold(self):
        extraction = legacy_open()
        extraction['parser_version'] = LEGACY_PARSER_VERSION
        extraction['extraction_method'] = 'tesseract_legacy_checkbox'
        row = extraction['transactions'][0]
        row.update(amount_low=50000001, amount_raw=None, ocr_confidence=40)
        qualified = qualify_automatic(extraction, IDENTITY)
        self.assertEqual(qualified['transactions'], [])
        blocked = next(item for item in qualified['quarantined'] if item['extraction_id']==row['extraction_id'])
        self.assertEqual(blocked['reasons'], ['ocr_confidence_below_threshold'])
        limitations = quarantined_limitations(extraction, qualified)
        self.assertEqual(len(limitations), 1)
        self.assertIn('Over $50,000,000', limitations[0])
        self.assertIn('ocr_confidence_below_threshold', limitations[0])
        self.assertIn('不作为合格交易', limitations[0])
        changed = deepcopy(qualified); changed['source_sha256']='c'*64
        with self.assertRaisesRegex(ValueError, 'differs'):
            quarantined_limitations(extraction, changed)
        row.update(amount_low=None, amount_kind=None)
        self.assertIsNone(threshold(row, LEGACY_PARSER_VERSION))

    def test_ambiguous_threshold_is_not_guessed(self):
        row=legacy_open()['transactions'][0]
        row['amount_low']=2000000
        self.assertIsNone(threshold(row, 'house-ptr-2026-04'))
        row['amount_low']=1000001
        self.assertIsNone(threshold(row, 'unknown-parser'))

    def test_refresh_cannot_rewrite_another_eligible_fact(self):
        extraction=legacy_open(); prior=qualify_automatic(extraction, IDENTITY)
        first=prior['transactions'][0]
        prior['quarantined'].append({'extraction_id':first['id'],
            'reasons':['open_ended_amount_not_representable']})
        prior['transactions']=[dict(first,id='unrelated-prior-fact')]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); relative=Path('2026/20000001')/('b'*64+'.json')
            target=root/'house_clerk/qualifications'/relative
            raw=root/'house_clerk/extractions'/relative
            target.parent.mkdir(parents=True);raw.parent.mkdir(parents=True)
            target.write_text(json.dumps(prior));raw.write_text(json.dumps(extraction))
            before=target.read_bytes()
            with self.assertRaisesRegex(ValueError,'another eligible fact'):
                refresh_qualifications(root, None)
            self.assertEqual(target.read_bytes(),before)

    def test_candidate_refresh_preserves_finite_objects_cutoff_and_rejects_unbound_open_rows(self):
        from test_house_candidate import BASE
        from unison_snapshot.house_candidate import _person
        extraction=legacy_open(); qualification=qualify_automatic(extraction, IDENTITY)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for folder,value in (('extractions',extraction),('qualifications',qualification)):
                path=root/f'house_clerk/{folder}/2026/20000001'/('b'*64+'.json')
                path.parent.mkdir(parents=True);path.write_text(json.dumps(value))
            base=deepcopy(BASE);base['people']=[_person(IDENTITY)];base['source_health']=[]
            base['transactions']=qualify_automatic(parse_word_pages(META,'a'*64,
                fixture_pages(),copy_allowed=True),IDENTITY)['transactions']
            original=deepcopy(base)
            result=refresh_candidate(base,root,None)
            self.assertEqual(len(result['transactions']),len(base['transactions'])+1)
            self.assertEqual(result['meta'],base['meta'])
            self.assertEqual(base,original)
            self.assertEqual(refresh_candidate(result,root,None),result)
            path=root/'house_clerk/qualifications/2026/20000001'/('b'*64+'.json')
            qualification['transactions'][0]['amount_low']=2
            path.write_text(json.dumps(qualification))
            with self.assertRaisesRegex(ValueError,'exact source qualification'):
                refresh_candidate(base,root,None)
            qualification=qualify_automatic(extraction,IDENTITY)
            path.write_text(json.dumps(qualification))
            base['meta']['data_cutoff_at']='2020-01-01T00:00:00Z'
            with self.assertRaisesRegex(ValueError,'existing source scope'):
                refresh_candidate(base,root,None)
