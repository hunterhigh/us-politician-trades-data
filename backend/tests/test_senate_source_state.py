"""Collection metadata reaches normal qualification without changing source facts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.codec import encode
from unison_snapshot.commands import senate_source_state as writer
from unison_snapshot.senate_candidate import build_senate_candidate
import test_senate_candidate as fixtures


class SenateSourceStateTests(unittest.TestCase):
    def test_review_materialization_preserves_normal_candidate_and_source_collisions_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);before=root/'before';review=root/'review';run=root/'run';run.mkdir()
            doc='11111111-1111-4111-8111-111111111111'
            item=fixtures.extraction(doc)
            state=fixtures.SenateCandidateTests().fixture(before,[fixtures.identity(doc)],[item])
            expected,audit=build_senate_candidate(before,state,deepcopy(fixtures.BASE))
            identities=json.loads(next((before/'senate_efd/identities').rglob('*.json')).read_bytes())
            identities['identity_counts']={'exact':1}
            discovery={'metadata':{'sha256':fixtures.CATALOG_SHA},'records_total':1,
                       'reports':[{'document_id':doc}]}
            reports={'archived_total':1,'pending_count':0,'failure_count':0}
            batch={'parser_version':fixtures.PARSER,'extractions':[item],'inspections':[],'failures':[]}
            for name,value in [('discovery',discovery),('identities',identities),
                               ('report-batch',reports),('extraction-batch',batch)]:
                (run/('senate-efd-'+name+'.json')).write_bytes(encode(value))
            writer.record_review(review,run,'2026-10-29T00:00:00+00:00')
            actual,actual_audit=build_senate_candidate(review,state,deepcopy(fixtures.BASE))
            self.assertEqual(encode(actual),encode(expected))
            self.assertEqual(encode(actual_audit),encode(audit))
            saved={str(p.relative_to(review)):p.read_bytes() for p in review.rglob('*.json')}
            writer.record_review(review,run,'2026-10-29T00:00:00+00:00')
            self.assertEqual(saved,{str(p.relative_to(review)):p.read_bytes() for p in review.rglob('*.json')})
            batch['extractions'][0]['transactions'][0]['asset_name_raw']='unexplained change'
            (run/'senate-efd-extraction-batch.json').write_bytes(encode(batch))
            with self.assertRaisesRegex(SystemExit,'conflicts with its source hash'):
                writer.record_review(review,run,'2026-10-29T00:00:00+00:00')
            self.assertEqual(saved,{str(p.relative_to(review)):p.read_bytes() for p in review.rglob('*.json')})


if __name__=='__main__':unittest.main()
