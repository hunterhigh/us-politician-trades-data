"""Conserve actual generic Spouse/DC facts under default execution.

Generated native sources test binding and consumption, not official accuracy.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from test_oge_default_explicit_owner_status import explicit_owner_inputs
from test_oge_owner_correction import CUTOFF
from unison_snapshot.codec import encode
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_candidate import build_oge_candidate


class DefaultExplicitOwnerCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-explicit-candidate-')
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)

    def default(self, data, *, base=None, roots=True):
        return build_oge_candidate(data['catalog'], [data['legacy']],
            data['generic'] if base is None else base, data_cutoff_at=CUTOFF,
            evidence_root=data['evidence_root'] if roots else None,
            source_extractions_by_document={data['legacy']['document_id']:data['legacy']})

    def test_actual_generic_output_is_kept_under_default_and_real_next_base_repeat(self):
        for owner in ('Spouse','Dependent Child'):
            with self.subTest(owner=owner):
                data = explicit_owner_inputs(self.root/owner,owner=owner)
                source_before = encode(data['legacy'])
                pdf_before = {str(path):path.read_bytes() for path in data['evidence_root'].rglob('*.pdf')}
                raw_id = data['legacy']['transactions'][0]['extraction_id']
                self.assertNotEqual(raw_id,data['prior']['id'])
                result,audit = self.default(data)
                self.assertEqual(result['transactions'],data['generic']['transactions'])
                self.assertEqual(result['people'],data['generic']['people'])
                self.assertNotIn(raw_id,{row['id'] for row in result['transactions']})
                report = audit['reports'][0]
                self.assertEqual(report['default_owner_conservation']['current_ids'],[data['prior']['id']])
                self.assertEqual(report['source_interpretation_binding']['parser_version'],'oge-278t-pdf/v2')
                self.assertEqual(report['source_interpretation_binding']['extraction_canonical_sha256'],hashlib.sha256(source_before).hexdigest())
                repeated,repeated_audit = self.default(data,base=result)
                self.assertEqual(encode(repeated),encode(result))
                self.assertEqual(encode(repeated_audit),encode(audit))
                self.assertEqual(encode(data['legacy']),source_before)
                self.assertTrue(all(Path(path).read_bytes()==raw for path,raw in pdf_before.items()))

    def test_unknown_and_wrong_complete_fact_are_preserved_with_independent_default_row(self):
        for unknown in (False,True):
            with self.subTest(unknown=unknown):
                data = explicit_owner_inputs(self.root/str(unknown),unknown=unknown,new_row=True)
                base = deepcopy(data['generic'])
                target = next(row for row in base['transactions'] if row['id']==data['prior']['id'])
                if not unknown:
                    target['amount_low'] = 1002
                result,audit = self.default(data,base=base)
                self.assertEqual(result['transactions'],base['transactions'])
                self.assertEqual(audit['current_qualified_transaction_count'],1)
                receipt = audit['reports'][0]['default_owner_conservation']
                self.assertEqual(receipt['current_ids'],[])
                self.assertEqual(receipt['deferred_ids'],[target['id']])
                self.assertEqual(next(row for row in result['transactions'] if row['id']!=target['id'])['owner'],'Self')
        data = explicit_owner_inputs(self.root/'missing-root')
        original = deepcopy(data['generic'])
        with self.assertRaisesRegex(OgeCatalogError,'original source root'):
            self.default(data,roots=False)
        self.assertEqual(data['generic'],original)

    def test_original_financial_quarantine_and_document_blocker_never_requalify_retained_id(self):
        data = explicit_owner_inputs(self.root/'exchange',exchange=True)
        raw_id = data['legacy']['quarantined'][0]['extraction_id']
        result,audit = self.default(data)
        self.assertEqual(result['transactions'],data['generic']['transactions'])
        self.assertIn(raw_id,{row['extraction_id'] for row in audit['reports'][0]['quarantined']})
        self.assertEqual(audit['current_qualified_transaction_count'],0)
        self.assertEqual(audit['reports'][0]['default_owner_conservation']['current_ids'],[])
        data = explicit_owner_inputs(self.root/'pending')
        data['catalog']['transactions'][0]['pending_final_oge_disposition'] = True
        metadata_path = next((data['evidence_root']/'oge/reports').rglob('*.json'))
        metadata = json.loads(metadata_path.read_bytes());metadata['pending_final_oge_disposition']=True
        metadata_path.write_bytes(encode(metadata))
        result,audit = self.default(data)
        self.assertEqual(result['transactions'],data['generic']['transactions'])
        self.assertIn('pending_final_oge_disposition',audit['reports'][0]['document_reasons'])
        self.assertEqual(audit['current_qualified_transaction_count'],0)

    def test_two_existing_claims_for_one_physical_row_do_not_choose_or_create_self_copy(self):
        data = explicit_owner_inputs(self.root,new_row=True)
        base = deepcopy(data['generic'])
        competing = deepcopy(data['prior'])
        competing.update(id=data['legacy']['transactions'][0]['extraction_id'],owner='Undisclosed')
        base['transactions'].append(competing)
        result,audit = self.default(data,base=base)
        self.assertEqual({row['id'] for row in result['transactions']},{row['id'] for row in base['transactions']})
        self.assertEqual(sorted(result['transactions'],key=lambda row:row['id']),sorted(base['transactions'],key=lambda row:row['id']))
        self.assertEqual(audit['current_qualified_transaction_count'],1)
        self.assertEqual(audit['reports'][0]['default_owner_conservation']['current_ids'],[])
        self.assertIn('default_owner_physical_correspondence_unresolved',
                      audit['reports'][0]['quarantined'][0]['reasons'])

    def test_nullable_representation_and_person_annotations_are_preserved(self):
        data = explicit_owner_inputs(self.root,owner='Dependent Child')
        base = deepcopy(data['generic'])
        for key in ('option_type','strike_price','expiration_date','position_effect_basis'):
            base['transactions'][0].pop(key)
        base['people'][0].update(priority=True,priority_reason='Existing note',portrait_url='https://example.invalid/portrait')
        result,audit = self.default(data,base=base)
        self.assertEqual(result['transactions'],base['transactions'])
        self.assertEqual(result['people'],base['people'])
        self.assertEqual(audit['current_qualified_transaction_count'],1)


if __name__=='__main__':
    unittest.main()
