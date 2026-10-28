"""History replay bounds and conservation stay independent of case-specific counts."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.commands.replay_senate_history import current_targets,selected_ids,verify_activation
import test_senate_history as controls


class HistoryReplayTests(unittest.TestCase):
    def test_selection_and_current_source_binding_fail_before_any_collection(self):
        for value in ['', '$(unsafe)', '11111111-1111-4111-8111-111111111111,',
                      ','.join(['11111111-1111-4111-8111-111111111111']*2)]:
            with self.assertRaises(ValueError):selected_ids(value)
        _,_,_,audit,_,extractions=controls.inputs([]);row=extractions[0];doc=row['document_id']
        with tempfile.TemporaryDirectory() as folder:
            review=Path(folder);target=review/'senate_efd/qualifications/current.json'
            target.parent.mkdir(parents=True);target.write_text(json.dumps(audit))
            extracted=review/'senate_efd/extractions'/doc/row['source_sha256']/(row['parser_version']+'.json')
            extracted.parent.mkdir(parents=True);extracted.write_text(json.dumps(row))
            self.assertEqual(current_targets(review,[doc]),audit)
            changed=deepcopy(row);changed['source_sha256']='f'*64;extracted.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'bound current'):
                current_targets(review,[doc])

    def test_primary_row_conservation_allows_annual_disclosures_but_rejects_predecessor_leaks(self):
        before={'input_transaction_count':2}
        after={'input_transaction_count':2,'quarantined_transaction_count':1,'superseded_report_transaction_count':0,
               'amendment_supplement_sha256':'a'*64,'amendment_supplement_report_count':1}
        supplement={'supplement_sha256':'a'*64,'target_count':1,'selected_document_ids':['prior']}
        candidate={'transactions':[{'filing_id':'current'},{'filing_id':'annual'}]}
        verify_activation(before,after,candidate,supplement,{'current'})
        candidate['transactions'].append({'filing_id':'prior'})
        with self.assertRaisesRegex(ValueError,'predecessor escaped'):
            verify_activation(before,after,candidate,supplement,{'current'})
        candidate['transactions'].pop();after['input_transaction_count']=3
        with self.assertRaisesRegex(ValueError,'conserve the fixed'):
            verify_activation(before,after,candidate,supplement,{'current'})


if __name__=='__main__':unittest.main()
