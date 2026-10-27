"""Explicit native owner IDs survive default selection without false current q."""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

from test_oge_candidate import base as empty_base
from test_oge_owner_correction import fixture, write_files, encoded, DOCUMENT, CUTOFF
from test_oge_owner_scope_status import unknown_external_text
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.disclosure_status_oge import build_oge_status


@lru_cache(maxsize=12)
def _generated(owner, unknown, new_row, exchange):
    heading = {'Spouse':'Spouse assets','Dependent Child':'Dependent children assets'}[owner]
    data = fixture(asset='Example Common Stock (EXM)',heading=heading,
        pdf_mutator=unknown_external_text if unknown else None,
        extra_rows=[['','Filer assets','','','',''],
                    ['2','Independent Stock (IND)','Purchase','06/02/2026','No','$15,001 - $50,000']]
        if new_row else None)
    _,_,review,evidence,_ = deepcopy(data)
    if exchange:
        # Both native readers already support this source table shape. v7 can
        # read Exchange; v2 preserves it as a financial quarantine. Alter the
        # actual original before hashing/rebuilding caches, never a saved row.
        meta = json.loads(next(raw for path,raw in evidence.items()
                               if '/reports/' in path and path.endswith('.json')))
        pdf = pymupdf.open(stream=evidence[meta['archive_path']],filetype='pdf')
        matches = 0
        for ref in pdf[1].get_contents():
            stream = pdf.xref_stream(ref)
            if b'5075726368617365' in stream:
                matches += stream.count(b'5075726368617365')
                pdf.update_stream(ref,stream.replace(b'5075726368617365',b'45786368616e6765'))
        if matches != 1:
            raise AssertionError('The exchange control requires one actual transaction text show')
        raw = pdf.tobytes(); pdf.close(); sha = hashlib.sha256(raw).hexdigest()
        relative = f'oge/reports/{DOCUMENT}/{sha}.pdf'
        evidence = {path:value for path,value in evidence.items() if '/reports/' not in path}
        evidence[relative] = raw
        evidence[relative[:-4]+'.json'] = encoded({**meta,'sha256':sha,'byte_length':len(raw),
                                                 'archive_path':relative})
        review = {path:value for path,value in review.items() if '/extractions/' not in path}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); write_files(root,evidence)
            metadata = root/(relative[:-4]+'.json')
            for generic in (False,True):
                parsed = parse_archived_pdf(root,metadata,generic=generic)
                review[f'oge/extractions/{DOCUMENT}/{sha}/{parsed["parser_version"].replace("/","-")}.json'] = encoded(parsed)
    return review,evidence


def explicit_owner_inputs(root, *, owner='Spouse',unknown=False,new_row=False,exchange=False):
    """Produce an actual normal generic snapshot to use as default's base."""
    root = Path(root); evidence_root,review_root = root/'evidence',root/'review'
    review,evidence = deepcopy(_generated(owner,unknown,new_row,exchange))
    write_files(evidence_root,evidence); write_files(review_root,review)
    catalog = json.loads(next(raw for path,raw in evidence.items() if '/catalog/normalized/' in path))
    legacy = json.loads(next(raw for path,raw in review.items() if path.endswith('v2.json')))
    native = json.loads(next(raw for path,raw in review.items() if path.endswith('v7.json')))
    generic,_ = build_oge_candidate(catalog,[native],empty_base(),data_cutoff_at=CUTOFF,
        evidence_root=evidence_root,legacy_extractions_by_document={DOCUMENT:legacy})
    prior = next(row for row in generic['transactions'] if row['owner'] == owner)
    return {'evidence_root':evidence_root,'review_root':review_root,'catalog':catalog,
            'legacy':legacy,'native':native,'generic':generic,'prior':prior}


class DefaultExplicitOwnerStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-explicit-default-status-')
        self.addCleanup(temporary.cleanup); self.root = Path(temporary.name)

    def default(self, data, *, prior=None):
        result,audit = build_oge_candidate(data['catalog'],[data['legacy']],
            data['generic'] if prior is None else prior,data_cutoff_at=CUTOFF,
            evidence_root=data['evidence_root'])
        self.save(data,audit)
        return result,audit

    @staticmethod
    def save(data,audit):
        write_files(data['review_root'],{'oge/qualifications/current.json':encoded(audit)})

    @staticmethod
    def status(data,result):
        return next(report for scope in build_oge_status(data['evidence_root'],data['review_root'],
            result['transactions'],[]) for report in scope['reports'] if report['report_id'] == DOCUMENT)

    def test_actual_generic_snapshot_keeps_explicit_native_ids_current_and_repeats(self):
        for owner in ('Spouse','Dependent Child'):
            with self.subTest(owner=owner):
                data = explicit_owner_inputs(self.root/owner,owner=owner)
                self.assertNotEqual(data['prior']['id'],data['legacy']['transactions'][0]['extraction_id'])
                result,audit = self.default(data)
                self.assertEqual(result['transactions'],data['generic']['transactions'])
                record = self.status(data,result)
                self.assertEqual(record['source_interpretation_binding']['parser_version'],'oge-278t-pdf/v2')
                self.assertEqual(record['qualified_transaction_count'],1)
                self.assertEqual(record['default_owner_conservation']['current_ids'],[data['prior']['id']])
                repeated,_ = self.default(data,prior=result)
                self.assertEqual(repeated['transactions'],result['transactions'])
                self.assertEqual(self.status(data,repeated)['qualified_transaction_count'],1)

    def test_unknown_scope_retains_complete_old_and_independent_default_row_remains_current(self):
        data = explicit_owner_inputs(self.root,unknown=True,new_row=True)
        result,audit = self.default(data)
        self.assertEqual(result['transactions'],data['generic']['transactions'])
        record = self.status(data,result)
        self.assertEqual(record['qualified_transaction_count'],1)
        self.assertEqual(record['qualification_state'],'partial')
        self.assertEqual(len(record['publication_transaction_ids']),2)
        self.assertEqual(record['default_owner_conservation']['deferred_ids'],[data['prior']['id']])
        audit['reports'][0].pop('default_owner_conservation'); self.save(data,audit)
        self.assertEqual(self.status(data,result)['qualified_transaction_count'],1)

    def test_missing_receipt_and_selection_with_only_default_cache_stays_unknown(self):
        data = explicit_owner_inputs(self.root,owner='Dependent Child',unknown=True,new_row=True)
        result,audit = self.default(data)
        report = audit['reports'][0]
        report.pop('default_owner_conservation'); report.pop('source_interpretation_binding')
        self.save(data,audit)
        (data['review_root']/f'oge/extractions/{DOCUMENT}/{data["legacy"]["source_sha256"]}/oge-278t-pdf-v7.json').unlink()
        record = self.status(data,result)
        self.assertIsNone(record['qualified_transaction_count'])
        self.assertEqual(record['qualification_state'],'unknown')
        self.assertEqual(len(record['publication_transaction_ids']),2)

    def test_raw2_actual_exchange_quarantine_maps_to_old_native_id(self):
        data = explicit_owner_inputs(self.root,exchange=True)
        self.assertEqual(data['legacy']['transactions'],[])
        self.assertEqual(len(data['native']['transactions']),1)
        raw_id = data['legacy']['quarantined'][0]['extraction_id']
        self.assertNotEqual(raw_id,data['prior']['id'])
        result,audit = self.default(data)
        self.assertEqual(result['transactions'],data['generic']['transactions'])
        record = self.status(data,result)
        self.assertEqual(record['qualified_transaction_count'],0)
        self.assertNotEqual(record['qualification_state'],'zero_transactions')
        self.assertEqual(record['publication_transaction_ids'],[data['prior']['id']])
        report = audit['reports'][0]
        self.assertIn(raw_id,{row['extraction_id'] for row in report['quarantined']})
        # Erasing only the receiver's duplicate diagnostic still cannot revive
        # a physical slot that the selected actual raw2 itself quarantines.
        report['quarantined'] = []; report['quarantined_count'] = 0
        self.save(data,audit)
        self.assertEqual(self.status(data,result)['qualified_transaction_count'],0)
        report['default_owner_conservation']['current_ids'] = [data['prior']['id']]
        report['default_owner_conservation']['deferred_ids'] = []
        self.save(data,audit)
        with self.assertRaisesRegex(ValueError,'conservation differs'):
            self.status(data,result)

    def test_full_formal_difference_and_receipt_tampering_cannot_restore_current(self):
        data = explicit_owner_inputs(self.root,new_row=True)
        old = deepcopy(data['generic'])
        target = next(row for row in old['transactions'] if row['id'] == data['prior']['id'])
        target['amount_low'] = 1002
        result,audit = self.default(data,prior=old)
        self.assertIn(target,result['transactions'])
        self.assertEqual(self.status(data,result)['qualified_transaction_count'],1)
        changed = deepcopy(audit)
        changed['reports'][0]['default_owner_conservation']['current_ids'] = [target['id']]
        changed['reports'][0]['default_owner_conservation']['deferred_ids'] = []
        self.save(data,changed)
        with self.assertRaisesRegex(ValueError,'conservation differs'):
            self.status(data,result)

    def test_receiver_raw2_row_blocker_is_translated_even_when_native_owner_proof_is_good(self):
        data = explicit_owner_inputs(self.root)
        result,audit = self.default(data)
        raw_id = data['legacy']['transactions'][0]['extraction_id']
        old_id = data['prior']['id']
        self.assertNotEqual(raw_id,old_id)
        self.assertEqual(self.status(data,result)['qualified_transaction_count'],1)
        # A receiver's financial blocker is independent of the source owner
        # proof. Its ID names the actual selected row, not the conserved ID.
        report = audit['reports'][0]
        report['quarantined'] = [{'extraction_id':raw_id,'reasons':['financial_row_conflict']}]
        report['quarantined_count'] = 1
        report['default_owner_conservation']['current_ids'] = []
        report['default_owner_conservation']['deferred_ids'] = [old_id]
        self.save(data,audit)
        self.assertEqual(self.status(data,result)['qualified_transaction_count'],0)
        report['default_owner_conservation']['current_ids'] = [old_id]
        report['default_owner_conservation']['deferred_ids'] = []
        self.save(data,audit)
        with self.assertRaisesRegex(ValueError,'conservation differs'):
            self.status(data,result)


if __name__ == '__main__':
    unittest.main()
