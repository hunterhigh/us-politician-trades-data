from copy import deepcopy
from pathlib import Path
import unittest

from unison_snapshot.corrections import load_correction
from unison_snapshot.reviewed_senate_paper import reviewed_record
from unison_snapshot.senate_candidate import _transaction


class ReviewedSenatePaperTests(unittest.TestCase):


    def test_shipped_reviews_allow_exact_records_and_refuse_unreviewed_variants(self):
        directory=Path(__file__).resolve().parents[1]/'data/corrections/senate_efd'
        count=expected_count=0
        for path in directory.glob('*.json'):
            correction=load_correction(path);template=correction['assertions']['report']
            expected_count+=template['eligible_transaction_count']
            for action in correction['actions']:
                row=action['value'];identity={'person_id':correction['assertions']['person_id']}
                self.assertTrue(reviewed_record(row,template,identity['person_id']))
                value,reasons=_transaction(row,template,identity)
                self.assertEqual(reasons,[])
                self.assertIsNone(value['instrument_type'])
                changed=deepcopy(row);changed['extraction_id']='senate-ptr:'+'0'*24
                self.assertFalse(reviewed_record(changed,template,identity['person_id']))
                changed=deepcopy(row);changed['asset_name_raw']='invented'
                self.assertFalse(reviewed_record(changed,template,identity['person_id']))
                count+=1
        self.assertGreater(count,0)
        self.assertEqual(count,expected_count)
