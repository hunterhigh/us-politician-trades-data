"""Official native Subholding Of is display context, never a new formal ID."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_house_ptr import fixture_pages,word,META,IDENTITY
import test_house_candidate as candidate_fixture
from unison_snapshot.house_ptr import parse_word_pages,qualify_automatic
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.house import HouseIndexError

SHA='a'*64


def pages():
    source=fixture_pages();first=[w for w in source[0]['words'] if 300<w['top']<390]
    source[0]['words']=[w for w in source[0]['words'] if w['top']<300]+deepcopy(first)
    second=deepcopy(first)
    for w in second:w['top']+=100
    source[0]['words']+=second
    for top,title in [(370,'Account One'),(470,'Account Two')]:
        source[0]['words'] += [word('Subholding',105,top,8.5),word('Of:',150,top,8.5),word(title,175,top,8.5)]
    return source


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')


def fixture(root,x,q):
    candidate_fixture.HouseCandidateTests().fixture(root)
    # Replace its sole existing qualification with this source-bound native one.
    old=root/'house_clerk/qualifications/2026/20000001/a.json';old.unlink()
    relative=f'2026/20000001/{SHA}.json'
    write(root/'house_clerk/qualifications'/relative,q);write(root/'house_clerk/extractions'/relative,x)
    write(root/'status/summary.json',{'parser_commit':'e180c4e68','pending_parse_count':0,'qualification_count':1,
       'qualified_transaction_count':len(q['transactions']),'quarantined_row_count':len(q['quarantined']),
       'evidence_count':1,'extracted_count':1,'failure_count':0})
    return {'status':'ok','run_at':'2026-09-18T12:00:00Z','counts':{'archived':1,'pending':0,'failed':0}}


class NativeAccountContextTests(unittest.TestCase):
    def test_exact_former_page_census_is_replayed_without_granting_new_coverage(self):
        from test_house_native_coverage import template
        from unison_snapshot.source_row_ledger import page_ledger, report_ledger
        from unison_snapshot.house_ptr import PARSER_VERSION
        source = template()
        closing = source[1]
        closing['words'] = [w for w in closing['words'] if w['top'] not in (104,131,200,220)]
        closing['native_page_geometry']['rects'] = []
        fresh = parse_word_pages(META,SHA,source,copy_allowed=True)
        current = fresh['source_report_ledger']
        self.assertEqual(current['relation_coverage'],'accounted')
        # The former grammar did not recognize a closing without a vehicle
        # section. Construct its stored unknown page independently, rather
        # than asking the compatibility implementation for its own expected value.
        former = report_ledger(source_id='house_clerk',document_sha256=SHA,
            adapter_version=PARSER_VERSION,page_count=2,
            pages=[deepcopy(current['pages'][0]),page_ledger(source_id='house_clerk',
                document_sha256=SHA,page_number=2,adapter_version=PARSER_VERSION,
                coverage='unknown',coverage_reason='native_row_anchors_not_complete_page_census',
                coordinate_frame='source_page_pdf_points',slots=[])],
            logical_rows=[{k:deepcopy(v) for k,v in row.items() if k!='qualification'}
                          for row in current['logical_rows']])
        self.assertEqual(former['relation_coverage'],'partial_unknown')
        saved = deepcopy(fresh);saved['source_report_ledger'] = former
        qualified = qualify_automatic(saved,IDENTITY)
        self.assertEqual(qualified['transactions'],qualify_automatic(fresh,IDENTITY)['transactions'])
        self.assertEqual(qualified['source_report_ledger']['relation_coverage'],'partial_unknown')
        self.assertEqual(saved['source_report_ledger'],former)
        for mutation in ('ledger','finance','words_digest'):
            changed = deepcopy(saved)
            if mutation=='ledger':changed['source_report_ledger']['pages'][1]['coverage_reason']='claimed_old'
            if mutation=='finance':changed['transactions'][0]['asset_name']='Forged'
            if mutation=='words_digest':changed['source_word_pages_sha256']='0'*64
            with self.assertRaises(HouseIndexError):qualify_automatic(changed,IDENTITY)

    def test_same_asset_different_official_accounts_are_two_source_rows_with_unchanged_ids(self):
        source=pages();before=deepcopy(source);x=parse_word_pages(META,SHA,source,copy_allowed=True)
        q=qualify_automatic(x,IDENTITY)
        self.assertEqual(len(q['transactions']),2)
        self.assertEqual([r['asset_name'] for r in q['transactions']],['Example Corp. — Account One','Example Corp. — Account Two'])
        self.assertEqual([r['id'] for r in q['transactions']],[r['extraction_id'] for r in x['transactions']])
        self.assertEqual(len({r['id'] for r in q['transactions']}),2)
        self.assertEqual([r['asset_name'] for r in x['transactions']],['Example Corp.','Example Corp.'])
        self.assertEqual(qualify_automatic(x,IDENTITY),q)
        self.assertEqual(source,before)

    def test_old_native_without_words_keeps_existing_facts_and_marks_display_pending(self):
        x=parse_word_pages(META,SHA,pages(),copy_allowed=True)
        x.pop('source_word_pages');x.pop('source_word_pages_sha256')
        q=qualify_automatic(x,IDENTITY)
        self.assertEqual([r['asset_name'] for r in q['transactions']],['Example Corp.','Example Corp.'])
        self.assertEqual(q['native_account_context']['status'],'source_pages_unavailable')
        # A legacy source-reviewed field is not reopened due to missing new metadata.
        q.pop('native_account_context');q['transactions'][0]['asset_name']='Preserved legacy spelling'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);state=fixture(root,x,q)
            c=build_house_candidate(root,state,deepcopy(candidate_fixture.BASE))
            self.assertEqual(c['transactions'],sorted(q['transactions'],key=lambda r:r['id']))
            self.assertIn('2 native account titles await saved source-word evidence',c['source_health'][0]['detail'])

    def test_tampered_source_title_words_digest_and_receipt_fail(self):
        for mutation in ['title','words','digest','receipt','finance','fake_name','missing_source']:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as d:
                x=parse_word_pages(META,SHA,pages(),copy_allowed=True);q=qualify_automatic(x,IDENTITY)
                if mutation=='title':x['transactions'][0]['subholding_of']='Forged account'
                if mutation=='words':x['source_word_pages'][0]['words'][-1]['text']='Forged account'
                if mutation=='digest':x['source_word_pages_sha256']='0'*64
                if mutation=='receipt':q['native_account_context']['rows'][0]['subholding_of_raw']='Forged account'
                if mutation=='finance':q['transactions'][0]['amount_high']=999999
                if mutation=='fake_name':q['transactions'][0]['asset_name']='Example Corp. — Forged account'
                root=Path(d);state=fixture(root,x,q)
                if mutation=='missing_source':(root/f'house_clerk/extractions/2026/20000001/{SHA}.json').unlink()
                with self.assertRaises(HouseIndexError):build_house_candidate(root,state,deepcopy(candidate_fixture.BASE))

    def test_missing_legacy_extraction_is_pending_display_not_zero_facts(self):
        x=parse_word_pages(META,SHA,pages(),copy_allowed=True)
        x.pop('source_word_pages');x.pop('source_word_pages_sha256');q=qualify_automatic(x,IDENTITY)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);state=fixture(root,x,q)
            (root/f'house_clerk/extractions/2026/20000001/{SHA}.json').unlink()
            c=build_house_candidate(root,state,deepcopy(candidate_fixture.BASE))
            self.assertEqual(c['transactions'],sorted(q['transactions'],key=lambda r:r['id']))
            self.assertIn('await missing source extraction',c['source_health'][0]['detail'])

    def test_old_qualification_plus_reproducible_source_is_refreshed_once(self):
        x=parse_word_pages(META,SHA,pages(),copy_allowed=True);q=qualify_automatic(x,IDENTITY)
        old=deepcopy(q);old.pop('native_account_context')
        for row in old['transactions']:row['asset_name']='Example Corp.'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);state=fixture(root,x,old);files={str(f):f.read_bytes() for f in root.rglob('*.json')}
            first=build_house_candidate(root,state,deepcopy(candidate_fixture.BASE))
            second=build_house_candidate(root,state,deepcopy(candidate_fixture.BASE))
            self.assertEqual(first,second)
            self.assertEqual(first['transactions'],sorted(q['transactions'],key=lambda r:r['id']))
            self.assertEqual({str(f):f.read_bytes() for f in root.rglob('*.json')},files)
            for before,after in zip(old['transactions'],q['transactions'],strict=True):
                self.assertEqual({k:v for k,v in before.items() if k!='asset_name'},
                                 {k:v for k,v in after.items() if k!='asset_name'})


if __name__=='__main__':unittest.main()
