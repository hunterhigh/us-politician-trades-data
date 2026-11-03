"""Parser upgrades conserve old facts and never turn account headers into holdings."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_house_holdings import page, word, extraction, IDENTITY, SHA
from test_house_partial_holdings import partial_input, candidate_fixture
from unison_snapshot.house_holdings import (PARSER_VERSION, extract_schedule_a_pages,
    qualify_financial_report, verify_holding_observation)
from unison_snapshot.house_annual_sections import observe_annual_sections
from unison_snapshot.house_holdings_replay import pending, replay_batch, write, read
from unison_snapshot.house_candidate import build_house_candidate
from unison_snapshot.house import HouseIndexError
from test_house_candidate import BASE


class HoldingParserReplayTests(unittest.TestCase):
    def test_bare_parent_heading_is_context_but_financial_cells_cannot_be_skipped(self):
        p=page();p['words'] += [word('Merrill Lynch Brokerage - MG [BK] ⇒',25,115)]
        rows,_=extract_schedule_a_pages([p],source_sha256=SHA)
        self.assertEqual(len(rows),2)
        x=extraction(rows);obs=observe_annual_sections([p],x)
        lines=[line for pg in obs['source_text_coverage']['pages'] for s in pg['sections'] for line in s['lines']]
        self.assertEqual(next(r for r in lines if 'Merrill' in r['text'])['disposition'],'explicit_account_heading')
        p['words'] += [word('Unreadable',321,115)]
        rows,_=extract_schedule_a_pages([p],source_sha256=SHA)
        obs=observe_annual_sections([p],extraction(rows))
        self.assertGreater(obs['source_text_coverage']['unresolved_financial_text_line_count'],0)

    def test_adjacent_repeated_printed_code_preserves_old_source_spelling(self):
        p=page();p['words'].append(word('[ST]',185,130))
        rows,_=extract_schedule_a_pages([p],source_sha256=SHA)
        self.assertEqual(rows[0]['asset_raw'],'Example Corp (EXM) [ST] [ST]')
        self.assertEqual(rows[0]['asset_name'],'Example Corp (EXM) [ST]')

    def continuation(self,value='None',leaf='[MF]'):
        first=page();first['words']=[w for w in first['words'] if float(w['top'])<=100]
        first['words'] += [word('Retirement ⇒ Example Fund (EXM)',25,700),word('SP',282,700),word(value,321,700)]
        second=page();second['words']=[w for w in second['words'] if float(w['top'])<=100]
        second['words'] += [word(token,25+index*90,130) for index,token in enumerate(leaf.split())]
        return [first,second]

    def test_code_only_page_continuation_does_not_quarantine_explicit_none(self):
        rows,_=extract_schedule_a_pages(self.continuation(),source_sha256=SHA)
        self.assertEqual(len(rows),1)
        q=qualify_financial_report(extraction(rows),IDENTITY)
        self.assertTrue(q['production_eligible'])
        self.assertEqual((len(q['holdings']),len(q['excluded']),len(q['quarantined'])),(0,1,0))
        pages=self.continuation('$15,001 - $50,000','(15140RCU1) [BA]')
        rows,_=extract_schedule_a_pages(pages,source_sha256=SHA)
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['value_low'],rows[0]['value_high']),(15001,50000))
        self.assertEqual(rows[0]['review_reasons'],[])
        self.assertEqual(rows[0]['asset_type_code'],'BA')
        self.assertIsNone(rows[0]['ticker'])  # A numeric CUSIP is not an inferred ticker.
        pages[1]['words'].append(word('JT',282,130))
        self.assertEqual(len(extract_schedule_a_pages(pages,source_sha256=SHA)[0]),2)

    def test_unresolved_schedule_b_does_not_reject_proven_partial_schedule_a(self):
        x,obs=partial_input()
        cov=x['source_text_coverage']
        cov['pages'].append({'sections':[{'section':'B','disposition_counts':{'unresolved_financial_text':1}}]})
        cov['unresolved_financial_text_line_count']=1
        self.assertTrue(qualify_financial_report(x,IDENTITY)['production_eligible'])
        cov['pages'][-1]['sections'][0]['section']='A'
        self.assertFalse(qualify_financial_report(x,IDENTITY)['production_eligible'])
        with self.assertRaises(HouseIndexError):verify_holding_observation(x,obs)

    def test_holding_proof_reaches_normal_candidate_without_adding_annual_b(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);before,q,state,relative=candidate_fixture(root)
            source=root/'house_clerk/holding_section_observations'/relative
            target=root/'house_clerk/holding_source_observations'/relative
            target.parent.mkdir(parents=True,exist_ok=True);source.replace(target)
            after=build_house_candidate(root,state,deepcopy(BASE))
            self.assertEqual(after['reported_holdings'],before['reported_holdings'])
            self.assertEqual(after['transactions'],before['transactions'])

    def test_versions_missing_proof_and_resume_after_assembly_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);evidence=root/'evidence';review=root/'review';x,obs=partial_input()
            x['parser_version']='older';rel=Path('2025/10000001')/(SHA+'.json')
            q=qualify_financial_report(x,IDENTITY)
            for folder,value in [('holding_extractions',x),('holding_qualifications',q),('holding_identities',IDENTITY)]:
                write(review/'house_clerk'/folder/rel,value)
            write(evidence/'house_clerk/financial-reports'/rel,x['source'])
            write(review/'status/house_holdings.json',{'failure_count':0,'pending_parse_count':0})
            fresh=deepcopy(x);fresh['parser_version']=PARSER_VERSION
            expected=qualify_financial_report(fresh,IDENTITY)
            self.assertEqual(pending(evidence,review),[rel])
            with patch('unison_snapshot.house_holdings_replay._reparse',return_value=(fresh,expected,obs)):
                result=replay_batch(evidence,review,parser_commit='a'*40,workers=1)
            self.assertEqual((result['selected'],result['remaining']),(1,0))
            result=replay_batch(evidence,review,parser_commit='a'*40,workers=1)
            self.assertTrue(result['requires_assembly'])
            status=read(review/'status/house_holdings.json');status['candidate_parser_version']=PARSER_VERSION
            write(review/'status/house_holdings.json',status)
            self.assertFalse(replay_batch(evidence,review,parser_commit='a'*40,workers=1)['requires_assembly'])
            (review/'house_clerk/holding_source_observations'/rel).unlink()
            self.assertEqual(pending(evidence,review),[rel])
            changed=deepcopy(expected);changed['holdings'][0]['value_low']=0
            before=(review/'house_clerk/holding_extractions'/rel).read_bytes()
            with patch('unison_snapshot.house_holdings_replay._reparse',return_value=(fresh,changed,obs)),self.assertRaises(HouseIndexError):
                replay_batch(evidence,review,parser_commit='a'*40,workers=1)
            self.assertEqual((review/'house_clerk/holding_extractions'/rel).read_bytes(),before)
