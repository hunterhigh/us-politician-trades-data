"""Budgeted archive progression, resume and transport/content boundaries."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from unison_snapshot.commands.drain_house_ptr import drain, save
from unison_snapshot.house import (archive_discovery, parse_index, HouseDocumentClient,
    HouseDownloadError, HouseIndexError)
from unison_snapshot.house_sync import plan_checkpoint
from test_house import fixture, ROW


class Clock:
    def __init__(self):self.value=0;self.delays=[]
    def now(self):return self.value
    def sleep(self,seconds):self.delays.append(seconds);self.value+=seconds


class Client:
    def __init__(self,clock,cases=None):self.clock=clock;self.calls=[];self.cases=cases or {}
    def download(self,candidate):
        self.calls.append(candidate.document_id);self.clock.value+=2
        actions=self.cases.get(candidate.document_id,[])
        item=actions.pop(0) if actions else None
        if isinstance(item,Exception):raise item
        return item or b'%PDF-1.7\ncontrol source\n%%EOF\n',{}


class HouseArchiveBudgetTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.evidence=self.root/'evidence';self.checkpoint=self.root/'checkpoint.json'

    def seed(self,count):
        raw=fixture(''.join(ROW.replace('20000001',str(20000001+i)) for i in range(count)))
        rows=parse_index(raw,2026);metadata=archive_discovery(self.evidence,2026,raw,{},rows)
        checkpoint=plan_checkpoint({'metadata':metadata,'filings':[asdict(r) for r in rows]},self.evidence,
            planned_at='2026-10-28T00:00:00Z');save(self.checkpoint,checkpoint)
        return checkpoint

    def run_budget(self,clock,client,budget):
        return drain(self.checkpoint,self.evidence,budget_seconds=budget,client=client,
            clock=clock.now,sleep=clock.sleep,utc_now=lambda:'2026-10-28T00:01:00Z')

    def test_budget_resumes_and_drains_more_than_ten_without_repeating_archives(self):
        initial=self.seed(20);clock=Clock();client=Client(clock)
        first=self.run_budget(clock,client,5)
        self.assertEqual((first['archived_count'],first['remaining_unarchived_count']), (2,18))
        self.assertEqual(first['stop_reason'],'time_budget_exhausted')
        saved=json.loads(self.checkpoint.read_bytes())
        self.assertEqual(saved['counts']['archived'],2)
        clock=Clock();rest=Client(clock);second=self.run_budget(clock,rest,100)
        self.assertEqual((second['archived_count'],second['remaining_unarchived_count']), (18,0))
        self.assertEqual(second['stop_reason'],'queue_drained')
        self.assertFalse(set(client.calls)&set(rest.calls))
        self.assertEqual(set(client.calls+rest.calls),set(initial['queue']))
        self.assertEqual(len(list(self.evidence.glob('house_clerk/documents/*/*/*.pdf'))),20)

    def test_only_temporary_transport_errors_retry_and_failed_work_remains_visible(self):
        initial=self.seed(2);first,second=initial['queue'];clock=Clock()
        client=Client(clock,{first:[HouseDownloadError('temporary',transient=True)]*2,
                             second:[b'not a PDF']})
        result=self.run_budget(clock,client,100)
        self.assertEqual((result['request_count'],result['retry_count']), (4,2))
        self.assertEqual((result['archived_count'],result['failed_count']), (1,1))
        self.assertEqual(result['stop_reason'],'retry_deferred')
        self.assertEqual(result['remaining_unarchived_count'],1)
        self.assertEqual(clock.delays[:2],[1,2])
        checkpoint=json.loads(self.checkpoint.read_bytes())
        self.assertEqual(checkpoint['documents'][second]['status'],'failed')
        self.assertIsNotNone(checkpoint['documents'][second]['next_retry_at'])

    def test_source_change_stops_before_download_or_checkpoint_mutation(self):
        initial=self.seed(1);path=next(self.evidence.glob('house_clerk/index/*/*.zip'))
        path.write_bytes(path.read_bytes()+b'changed');before=self.checkpoint.read_bytes();clock=Clock();client=Client(clock)
        with self.assertRaisesRegex(HouseIndexError,'index hash changed'):
            self.run_budget(clock,client,100)
        self.assertEqual(client.calls,[]);self.assertEqual(self.checkpoint.read_bytes(),before)

    def test_real_download_types_do_not_retry_a_permanent_http_failure(self):
        candidate=parse_index(fixture(ROW),2026)[0]
        for status,transient in [(403,False),(429,True),(503,True)]:
            with self.subTest(status=status),patch('urllib.request.urlopen',side_effect=HTTPError(candidate.document_url,status,'error',{},None)):
                with self.assertRaises(HouseDownloadError) as failure:HouseDocumentClient().download(candidate)
                self.assertEqual((failure.exception.http_status,failure.exception.transient),(status,transient))
        with patch('urllib.request.urlopen',side_effect=URLError('offline')):
            with self.assertRaises(HouseDownloadError) as failure:HouseDocumentClient().download(candidate)
            self.assertTrue(failure.exception.transient)


if __name__=='__main__':unittest.main()
