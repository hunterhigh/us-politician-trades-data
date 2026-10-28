from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.commands.oge_source_state import record
from unison_snapshot.oge import discover_catalog, OgeSourceConfig
from test_oge import DIRECT, payload, row


class OgeStateTests(unittest.TestCase):
    def test_repeated_verified_catalog_has_a_new_observation_but_an_unchanged_archive(self):
        class Client:
            def download_page(self, **kwargs):
                return json.dumps(payload([row(DIRECT)])).encode(), {'content-type':'application/json'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            class Clock(datetime):
                instant=datetime(2026,10,20,tzinfo=timezone.utc)
                @classmethod
                def now(cls,tz=None):return cls.instant
            with patch('unison_snapshot.oge.datetime',Clock):
                first=discover_catalog(root,OgeSourceConfig(True,True),client=Client())
                archived=(root/first['metadata']['archive_path']).read_bytes()
                Clock.instant=datetime(2026,10,29,tzinfo=timezone.utc)
                second=discover_catalog(root,OgeSourceConfig(True,True),client=Client())
            self.assertEqual(first['metadata'],second['metadata'])
            self.assertEqual((root/first['metadata']['archive_path']).read_bytes(),archived)
            self.assertNotEqual(first['observed_at'],second['observed_at'])

    def test_failed_attempt_and_gate_do_not_advance_the_last_success(self):
        gate={'status':'enabled','collection_enabled':True,'terms_acknowledged':True}
        catalog={'metadata':{'retrieved_at':'2026-10-20T00:00:00Z'},'observed_at':'2026-10-29T00:00:00Z'}
        reports={'catalog_direct_count':1,'reports':[{}],'pending_count':0,'failure_count':0,'request_required_count':2}
        extraction={key:0 for key in ['failure_count','extraction_count','reused_extraction_count',
            'parsed_extraction_count','transaction_count','quarantined_row_count']}
        with tempfile.TemporaryDirectory() as directory:
            value=record(Path(directory),gate,catalog=catalog,reports=reports,extraction=extraction,successful=True)
            self.assertEqual(value['last_successful_collection_at'],catalog['observed_at'])
            pending=record(Path(directory),gate)
            failed=record(Path(directory),gate,successful=False,failure={'http_status':403,'failed_step':'collect_catalog'})
            for result in [pending,failed]:
                self.assertEqual(result['last_successful_collection_at'],catalog['observed_at'])
                self.assertEqual(result['catalog'],catalog['metadata'])
            self.assertEqual(failed['last_collection_attempt']['http_status'],403)


if __name__=='__main__':unittest.main()
