"""Approved OGE annual policy: synthetic source contracts, never accuracy gold."""
from copy import deepcopy
import unittest
from test_oge_annual_candidate import source, inputs, base, ledger, DOC
from test_public_repo import FakeTransport
from unison_snapshot.whitehouse_annual_candidate import overlay_oge_annual_candidate
from unison_snapshot.builder import build, normalize
from unison_snapshot.legacy import load, processor_version_for
from unison_snapshot.public_repo import PublicSnapshotRepository


def complete_case():
    extraction = source(official_unknown=True)
    return extraction, overlay_oge_annual_candidate(base(), inputs(extraction))


class ApprovedOgeAnnualPolicyTests(unittest.TestCase):
    def test_existing_overlay_cli_then_public_reader_base_repeat_is_idempotent(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import tempfile
        root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);(folder/'review').mkdir()
            (folder/'base.json').write_text(json.dumps(base()),encoding='utf-8')
            (folder/'input.json').write_text(json.dumps(inputs(source(official_unknown=True))),encoding='utf-8')
            args=[sys.executable,str(root/'backend/scripts/overlay_whitehouse_annual_candidate.py'),
                '--review-root',str(folder/'review'),'--candidate',str(folder/'base.json'),
                '--audit-out',str(folder/'audit.json'),'--oge-annual-input',str(folder/'input.json')]
            env=dict(os.environ,PYTHONPATH=str(root/'backend/src'))
            subprocess.run(args,cwd=root,env=env,check=True,capture_output=True)
            first=json.loads((folder/'base.json').read_bytes())
            subprocess.run(args,cwd=root,env=env,check=True,capture_output=True)
            self.assertEqual(json.loads((folder/'base.json').read_bytes()),first)
            bundle=build(first,generated_at=first['meta']['data_cutoff_at'],allow_production=True)
            selected=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('dashboard').snapshot
            (folder/'base.json').write_text(json.dumps(selected),encoding='utf-8')
            subprocess.run(args,cwd=root,env=env,check=True,capture_output=True)
            self.assertEqual(json.loads((folder/'base.json').read_bytes()),selected)
            audit=json.loads((folder/'audit.json').read_bytes())['oge_annual']
            self.assertEqual(audit['added_transaction_count'],0)
            self.assertEqual(len(audit['reports'][0]['retained_transaction_ids']),1)

    def test_complete_sections_only_official_unknown_show_known_leaves_and_transaction(self):
        extraction, (candidate, audit) = complete_case()
        self.assertEqual((len(candidate['reported_holdings']), len(candidate['transactions'])), (2, 1))
        coverage = candidate['meta']['holding_report_coverage'][0]
        self.assertFalse(coverage['complete_total_assets'])
        self.assertEqual(coverage['unresolved_rows'][0]['value_raw'], 'value not readily ascertainable')
        self.assertEqual(coverage['observed_row_count'], 4)  # 2 known + official None + 1 unknown
        self.assertEqual((coverage['finite_value_low'], coverage['finite_value_high']), (16002, 65000))
        self.assertEqual(audit['added_transaction_count'], 1)
        self.assertEqual(ledger(extraction)['part7_opt_in'], 'independent_disclosure_rows/v1')
        again, second = overlay_oge_annual_candidate(candidate, inputs(extraction))
        self.assertEqual(again, candidate)
        self.assertEqual(second['added_transaction_count'], 0)
        self.assertEqual(second['reports'][0]['retained_transaction_ids'], [candidate['transactions'][0]['id']])
        bundle = build(candidate, generated_at=candidate['meta']['data_cutoff_at'], allow_production=True)
        person = candidate['people'][0]['id']
        for mode, key in [('dashboard', None), ('search', None), ('person', person)]:
            selected = PublicSnapshotRepository('example', 'data', transport=FakeTransport(bundle.files)).fetch(mode, key).snapshot
            processed = load('process_snapshot', version='v3').build_snapshot(selected)
            self.assertEqual(len(processed['transactions']), 1)
            self.assertEqual(len(processed['meta']['transaction_disclosure_relations']), 1)
            self.assertEqual(len(processed['meta']['holding_report_coverage']), 1)
            if mode == 'dashboard':
                repeated, repeat_audit = overlay_oge_annual_candidate(selected, inputs(extraction))
                self.assertEqual(repeated, selected)
                self.assertFalse(repeat_audit['reports'][0]['transaction_field_differences'])

    def test_bad_holding_does_not_block_good_part7_and_does_not_get_gap_exception(self):
        for change in ['unread', 'number_only', 'phrase_only', 'conflicting_band', 'unknown_scope', 'missing_page', 'duplicate']:
            with self.subTest(change=change):
                extraction = source(official_unknown=True)
                row = extraction['quarantined'][0]
                if change == 'unread':
                    row.update(asset_name='Unread asset', value_disclosure_status='value_cell_text_empty')
                elif change == 'number_only':
                    row.update(asset_name='', owner='Unknown', reasons=['asset_name_unreadable', 'holding_value_missing'])
                elif change == 'phrase_only':
                    row['asset_name']='(value not readily ascertainable)'
                elif change == 'conflicting_band':
                    row.update(raw_value='$1,001 - $15,000', reasons=['asset_value_description_conflicts_with_band'])
                elif change == 'unknown_scope':
                    row['account_context'] = {'status':'unknown'}
                elif change == 'duplicate':
                    extraction['quarantined'].append(deepcopy(row))
                else:
                    extraction['source_coverage_evidence']['pages'].pop()
                candidate, audit = overlay_oge_annual_candidate(base(), inputs(extraction))
                self.assertFalse(candidate['reported_holdings'])
                self.assertNotIn('holding_report_coverage', candidate['meta'])
                self.assertEqual(len(candidate['transactions']), 1)
                self.assertIn('partial_report_policy_pending', audit['reports'][0]['report_reasons'])

    def test_previous_period_values_do_not_enter_current_gap_total(self):
        extraction,(candidate,_)=complete_case()
        old=deepcopy(candidate['reported_holdings'][0])
        old.update(id='synthetic-prior-period',filing_id='b'*32,report_period_end='2024-12-31',value_low=999999,value_high=999999)
        context=base();context['people']=candidate['people'];context['reported_holdings']=[old]
        selected,_=overlay_oge_annual_candidate(context,inputs(extraction))
        self.assertEqual(next(r for r in selected['reported_holdings'] if r['id']==old['id']),old)
        coverage=selected['meta']['holding_report_coverage'][0]
        self.assertEqual((coverage['finite_value_low'],coverage['finite_value_high']),(16002,65000))
        self.assertNotIn(old['id'],coverage['published_holding_ids'])
        bundle=build(selected,generated_at=selected['meta']['data_cutoff_at'],allow_production=True)
        read=PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('dashboard').snapshot
        processed=load('process_snapshot',version='v3').build_snapshot(read)
        self.assertEqual(processed['meta']['holding_report_coverage'][0],coverage)

    def test_missing_date_and_unknown_owner_remain_real_blockers(self):
        extraction = source(official_unknown=True)
        extraction['cover_date_evidence']['cover_text'] = extraction['cover_date_evidence']['cover_text'].replace('06/01/2026','unreadable')
        candidate, audit = overlay_oge_annual_candidate(base(), inputs(extraction))
        self.assertFalse(candidate['transactions'])
        self.assertFalse(candidate['reported_holdings'])
        tx = next(r for r in audit['reports'][0]['row_qualification'] if r['section']=='part7')
        self.assertEqual(tx['reasons'], ['filing_date_unverified'])
        extraction = source()
        extraction['transactions'][0]['owner'] = 'Unknown'
        candidate, audit = overlay_oge_annual_candidate(base(), inputs(extraction))
        self.assertFalse(candidate['transactions'])
        self.assertEqual(len(candidate['reported_holdings']), 2)
        self.assertIn('owner_unverified', next(r for r in audit['reports'][0]['row_qualification'] if r['section']=='part7')['reasons'])

    def test_possible_ptr_overlap_is_annotation_only_and_ticker_subset_loads_no_other_ptr(self):
        extraction, (candidate, _) = complete_case()
        annual = candidate['transactions'][0]
        ptr = deepcopy(annual)
        ptr.update(id='oge-278t:'+'b'*24, filing_id='b'*32, ticker='ABC', ticker_mapping_basis='explicit_source_ticker')
        context = base(); context['people'] = candidate['people']; context['transactions'] = [ptr]
        candidate, _ = overlay_oge_annual_candidate(context, inputs(extraction))
        self.assertEqual(len(candidate['transactions']), 2)
        self.assertEqual(next(r for r in candidate['transactions'] if r['id']==ptr['id']), ptr)
        self.assertEqual(candidate['meta']['transaction_disclosure_relations'][0]['possible_ptr_transaction_ids'], [ptr['id']])
        bundle = build(candidate, generated_at=candidate['meta']['data_cutoff_at'], allow_production=True)
        selected = PublicSnapshotRepository('example','data',transport=FakeTransport(bundle.files)).fetch('ticker','ABC').snapshot
        self.assertEqual(len(selected['transactions']), 1)
        self.assertFalse(selected['meta']['transaction_disclosure_relations'])
        load('process_snapshot',version='v3').build_snapshot(selected)

    def test_relationship_missing_or_tampered_rejected_and_original_source_identity_kept(self):
        extraction, (candidate, _) = complete_case()
        decision = next(r for r in ledger(extraction)['rows'] if r['observations'][0]['conditions']['section']=='part7')
        self.assertEqual(candidate['transactions'][0]['id'], decision['candidate_id'])
        relation = candidate['meta']['transaction_disclosure_relations'][0]
        self.assertEqual(relation['source_row_identity_sha256'], decision['source_row_identity_sha256'])
        for mutation in ['missing', 'empty', 'digest', 'source', 'pdf_sha', 'page', 'printed', 'period', 'unknown_literal']:
            changed = deepcopy(candidate)
            if mutation=='missing':changed['meta'].pop('transaction_disclosure_relations')
            elif mutation=='empty':changed['meta']['transaction_disclosure_relations']=[]
            elif mutation=='digest':changed['meta']['transaction_disclosure_relations'][0]['source_row_identity_sha256']='a'*64
            elif mutation=='source':changed['meta']['transaction_disclosure_relations'][0]['filing_id']='b'*32
            elif mutation=='pdf_sha':changed['meta']['transaction_disclosure_relations'][0]['source_sha256']='b'*64
            elif mutation=='page':changed['meta']['transaction_disclosure_relations'][0]['page_number']+=1
            elif mutation=='printed':changed['meta']['transaction_disclosure_relations'][0]['printed_row_number']='22'
            elif mutation=='period':changed['meta']['transaction_disclosure_relations'][0]['report_period_end']='2024-12-31'
            else:changed['meta']['holding_report_coverage'][0]['unresolved_rows'][0]['value_raw']='unreadable'
            self.assertEqual(processor_version_for(changed),'v3')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build(changed,generated_at=changed['meta']['data_cutoff_at'],allow_production=True)

    def test_qualification_rule_key_changes_but_original_row_identity_does_not(self):
        import hashlib
        import json
        from unison_snapshot.pipeline_ledger import idempotency_key
        extraction=source(); actual=ledger(extraction)
        decision=next(r for r,s in zip(actual['rows'],actual['detected_source_rows']) if s['section']=='part7')
        row=extraction['transactions'][0]
        identity=('part7', row['page_number'],str(row['row_number']),json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')))
        original='oge-annual-row:'+hashlib.sha256(f"{extraction['source_document_id']}|{extraction['source_sha256']}|{identity}|0".encode()).hexdigest()[:24]
        self.assertEqual(decision['candidate_id'],original)
        parser=decision['parser']
        old_key=idempotency_key(source_id='oge',source_sha256=extraction['source_sha256'],parser_id=parser['parser_id'],
            parser_version=parser['parser_version'],rules_version='oge-278e-row-qualification/v1',layout_fingerprint=parser['layout_fingerprint'])
        self.assertEqual(parser['rules_version'],'oge-278e-row-qualification/v2')
        self.assertNotEqual(decision['idempotency_key'],old_key)

    def test_same_source_copy_revision_and_old_field_changes_never_silently_duplicate_or_correct(self):
        extraction, (first, _) = complete_case()
        for mode in ['copy','amendment','field_change','changed_row']:
            changed = deepcopy(extraction); data=inputs(changed); context=deepcopy(first)
            if mode in {'copy','amendment'}:
                changed['source_document_id']='b'*32
                changed['evidence_archive_path']=changed['evidence_archive_path'].replace(DOC,'b'*32)
                meta=data['archive_metadata_by_document'].pop(DOC);meta.update(document_id='b'*32,archive_path=changed['evidence_archive_path'])
                if mode=='amendment':meta['amended_label']='Amendment'
                data['archive_metadata_by_document']['b'*32]=meta
            elif mode=='field_change':
                context['transactions'][0]['amount_low']+=1
            else:
                changed['transactions'][0]['asset_name']='Different asset'
            result,audit=overlay_oge_annual_candidate(context,data)
            self.assertEqual(result,context)
            self.assertEqual(audit['added_transaction_count'],0)
            report=audit['reports'][0]
            self.assertTrue(report['transaction_report_reasons'] or report['transaction_field_differences'])

    def test_same_batch_exact_copies_choose_one_original_identity_and_status_stays_outside_ptr_scope(self):
        import json
        import tempfile
        from pathlib import Path
        from unison_snapshot.disclosure_status_oge import build_oge_status
        extraction = source(official_unknown=True)
        other = deepcopy(extraction)
        other.update(source_document_id='b'*32, evidence_archive_path=extraction['evidence_archive_path'].replace(DOC,'b'*32))
        data = inputs(extraction)
        metadata = deepcopy(data['archive_metadata_by_document'][DOC])
        metadata.update(document_id='b'*32, archive_path=other['evidence_archive_path'])
        data['archive_metadata_by_document']['b'*32] = metadata
        data['extractions'].append(other)
        candidate,audit = overlay_oge_annual_candidate(base(),data)
        self.assertEqual(len(candidate['transactions']),1)
        self.assertEqual(audit['reports'][1]['disposition'],'excluded_exact_source_copy')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); evidence=root/'evidence';review=root/'review'; review.mkdir()
            path=evidence/extraction['evidence_archive_path'].replace('.pdf','.json')
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(data['archive_metadata_by_document'][DOC]),encoding='utf-8')
            direct,_,_=build_oge_status(evidence,review,candidate['transactions'],candidate['reported_holdings'])
            self.assertEqual(direct['reports'],[])
            self.assertEqual(direct['unmatched_publication_transaction_ids'],[candidate['transactions'][0]['id']])
            self.assertIn('oge_direct_278e_outside_current_278t_status_scope',direct['limitations'])

if __name__=='__main__':unittest.main()
