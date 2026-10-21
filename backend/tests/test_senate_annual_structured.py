"""Opt-in source-derived annual display corrections retain the v3 identity."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_annual import (extract_annual, build_annual_review,
    overlay_annual_candidate, STRUCTURED_PARSER_VERSION, DISCOVERY_SCHEMA, _json)
from tests.test_senate_annual import annual_html
from tests.test_senate_identity import member, roster

DOC = '11111111-1111-4111-8111-000000000001'
URL = f'https://efdsearch.senate.gov/search/view/annual/{DOC}/'


def metadata(raw):
    return {'schema_version': 'senate-efd-annual-archive/v1', 'document_id': DOC,
        'document_url': URL, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'byte_length': len(raw),
        'filer_name': 'Ada Example', 'office': 'Example, Ada (Senator)',
        'report_year': 2025, 'amendment_number': 0, 'portal_listed_date': '2026-05-01'}


def sample():
    return annual_html().replace(b'ACME - Acme Corp',
        b'<strong class="marginit-right">ACME - Acme Corp</strong>'
        b'<div class="muted"><em>Description:</em> Official business description</div>')


class StructuredAnnualTests(unittest.TestCase):
    def fixture(self, root):
        raw=sample();m=metadata(raw);e=root/'e';r=root/'r';folder=e/'senate_efd/annual/reports'/DOC
        folder.mkdir(parents=True)
        (folder/(m['source_sha256']+'.html')).write_bytes(raw)
        (folder/(m['source_sha256']+'.metadata.json')).write_bytes(_json(m))
        p=e/'senate_efd/annual/catalog/current.json';p.parent.mkdir(parents=True)
        p.write_bytes(_json({'schema_version':DISCOVERY_SCHEMA,'source_id':'senate_efd',
            'catalog_record_count':1,'annual_report_count':1,'nonannual_count':0,
            'reports':[{k:m[k] for k in ['document_id','document_url','filer_name','office',
                'portal_listed_date','report_year','amendment_number']}]}))
        members=roster(member('A000001','Ada','Example'))
        return e,r,members,raw

    def test_structured_label_retains_old_id_and_all_non_display_fields(self):
        raw=sample();old=extract_annual(metadata(raw),raw)
        new=extract_annual(metadata(raw),raw,structured_asset_labels=True)
        self.assertEqual(old,extract_annual(metadata(raw),raw,structured_asset_labels=False))
        self.assertEqual(new['parser_version'],STRUCTURED_PARSER_VERSION)
        a,b=old['rows'][0],new['rows'][0]
        self.assertIn('CorpDescription:',a['asset_name'])
        self.assertEqual(b['asset_name'],'Acme Corp — Description: Official business description')
        self.assertEqual(b['asset_label_source']['official_notes'],'Description: Official business description')
        for key in a:
            if key!='asset_name':self.assertEqual(a[key],b[key],key)
        self.assertEqual(b['ticker'],'ACME')

    def test_plain_cell_supported_but_two_primary_labels_remain_ambiguous(self):
        raw=annual_html();a=extract_annual(metadata(raw),raw,structured_asset_labels=True)
        self.assertEqual(a['rows'][0]['asset_name'],'Acme Corp')
        changed=sample().replace(b'</strong>',b'</strong><strong>Second competing label</strong>',1)
        row=extract_annual(metadata(changed),changed,structured_asset_labels=True)['rows'][0]
        self.assertEqual(row['disposition'],'quarantined')
        self.assertIn('asset_primary_label_ambiguous',row['reasons'])

    def test_parent_notes_do_not_become_child_asset_name_or_change_old_id(self):
        raw=sample().replace(b'<tr><td>1</td>',b'<tr><td>1</td><td><strong>Account</strong><div class="muted">Description: Account note</div></td><td>Bank</td><td>Self</td><td>--</td><td></td><td></td></tr><tr><td>1.1</td>',1)
        old=extract_annual(metadata(raw),raw);new=extract_annual(metadata(raw),raw,structured_asset_labels=True)
        self.assertEqual(old['rows'][1]['extraction_id'],new['rows'][1]['extraction_id'])
        self.assertEqual(new['rows'][1]['asset_name'],'Account — Description: Account note / ACME - Acme Corp — Description: Official business description')
        self.assertIn('Account note',new['rows'][0]['asset_label_source']['official_notes'])
        self.assertEqual(new['rows'][1]['ticker'],old['rows'][1]['ticker'])

    def test_checking_name_change_and_same_named_account_notes_stay_visible(self):
        raw=annual_html().replace(b'ACME - Acme Corp',
            b'<strong>Same Bank</strong><div class="muted">Type: Checking</div>')
        raw=raw.replace(b'Old Income Asset',
            b'<strong>Same Bank</strong><div class="muted">Filer comment: Name change; formerly Old Bank. Type: IRA</div>')
        raw=raw.replace(b'None (or less than $1,001)',b'$1,001 - $15,000')
        old=extract_annual(metadata(raw),raw);new=extract_annual(metadata(raw),raw,structured_asset_labels=True)
        self.assertEqual([r['extraction_id'] for r in old['rows']],[r['extraction_id'] for r in new['rows']])
        self.assertEqual(new['rows'][0]['asset_name'],'Same Bank — Type: Checking')
        self.assertEqual(new['rows'][1]['asset_name'],'Same Bank — Filer comment: Name change; formerly Old Bank. Type: IRA')
        self.assertNotEqual(new['rows'][0]['asset_name'],new['rows'][1]['asset_name'])
        for before,after in zip(old['rows'],new['rows'],strict=True):
            self.assertEqual(''.join(before['asset_name'].split()),''.join(after['asset_name'].replace(' — ','').split()))

    def test_ambiguous_parent_label_does_not_supply_child_context(self):
        raw=sample().replace(b'<tr><td>1</td>',
            b'<tr><td>1</td><td><strong>Account A</strong><strong>Account B</strong></td><td>Bank</td><td>Self</td><td>--</td><td></td><td></td></tr><tr><td>1.1</td>',1)
        rows=extract_annual(metadata(raw),raw,structured_asset_labels=True)['rows']
        self.assertEqual(rows[1]['disposition'],'quarantined')
        self.assertIn('asset_primary_label_ambiguous',rows[1]['reasons'])

    def test_real_consumer_replays_html_and_rejects_label_or_id_or_notes_tampering(self):
        from unison_snapshot.builder import build
        from tests.test_senate_candidate import BASE
        with tempfile.TemporaryDirectory() as folder:
            e,r,members,raw=self.fixture(Path(folder))
            old=build_annual_review(e,r,members)
            annual=build_annual_review(e,r,members,structured_asset_labels=True)
            self.assertEqual([x['id'] for x in old['reported_holdings']],[x['id'] for x in annual['reported_holdings']])
            self.assertEqual(len(annual['asset_label_differences']),1)
            base=deepcopy(BASE)
            base["source_health"]=[]
            candidate,_=overlay_annual_candidate(base,r,expected_roster_sha256=members['metadata']['sha256'],evidence_root=e)
            self.assertEqual(candidate['reported_holdings'][0]['asset_name'],'Acme Corp — Description: Official business description')
            self.assertTrue(build(candidate,generated_at='2026-10-20T00:00:00Z',allow_production=True).manifest['snapshot_id'])
            current=r/'senate_efd/annual/current.json'
            for field in ['asset_name','id','value_low']:
                changed=deepcopy(annual)
                changed['reported_holdings'][0][field]='tampered' if field!='value_low' else 2
                current.write_bytes(_json(changed))
                with self.subTest(field=field),self.assertRaisesRegex(SenateEfdError,'source projection'):
                    overlay_annual_candidate(deepcopy(base),r,expected_roster_sha256=members['metadata']['sha256'],evidence_root=e)
            hidden=deepcopy(annual);hidden.pop('asset_label_mode');hidden.pop('asset_label_differences')
            current.write_bytes(_json(hidden))
            with self.assertRaisesRegex(SenateEfdError,'archived source evidence'):
                overlay_annual_candidate(deepcopy(base),r,expected_roster_sha256=members['metadata']['sha256'],evidence_root=e)
            current.write_bytes(_json(annual))
            path=r/annual['reports'][0]['extraction_path'];saved=path.read_bytes();changed=json.loads(saved)
            changed['rows'][0]['asset_label_source']['official_notes']='made up'
            path.write_bytes(_json(changed))
            with self.assertRaisesRegex(SenateEfdError,'archived HTML'):
                overlay_annual_candidate(deepcopy(base),r,expected_roster_sha256=members['metadata']['sha256'],evidence_root=e)
            path.write_bytes(saved)
            with self.assertRaisesRegex(SenateEfdError,'archived source evidence'):
                overlay_annual_candidate(deepcopy(base),r,expected_roster_sha256=members['metadata']['sha256'])
