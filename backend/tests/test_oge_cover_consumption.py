"""Original cover objects through saved readings, candidate and public status."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pymupdf
from test_oge_native_cover_roles import native_cover
from test_oge_native_page_roles import native_privacy
from test_oge_candidate import direct, catalog, base, DOCUMENT_ID
from test_oge_content_relations import saved
from test_oge_observed_candidate import original
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_transaction_adapter import adapt_oge_278t_extraction, materialize_oge_278t_observations, _cover_identity_context
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.oge_278t_page_coverage import inspect_pdf
from unison_snapshot.source_row_ledger import oge_278t_shadow_pages

CUTOFF = '2026-06-20T00:00:00Z'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


def fixture(root, *, role='cover', mutation=None):
    path = root/'source.pdf'
    if role == 'cover':
        context = native_cover(path, mutation=mutation)
    else:
        native_privacy(path, mutation=mutation)
        context = dict(filer_name='EXAMPLE, ADA', agency='Economic Office', position_title='Senior Analyst')
    document = pymupdf.open(path); document.new_page(width=792, height=792)
    raw = document.tobytes(); document.close()
    sha = hashlib.sha256(raw).hexdigest(); context['source_sha256'] = sha
    evidence, review = root/'evidence', root/'review'
    relative = f'oge/reports/{DOCUMENT_ID}/{sha}.pdf'
    source = evidence/relative; source.parent.mkdir(parents=True); source.write_bytes(raw)
    record = direct(agency=context['agency'])
    record.update(filer_name=context['filer_name'], position_title=context['position_title'], catalog_added_date='2026-06-20')
    old = original(); old.update(source_sha256=sha, catalog_filer_name=record['filer_name'],
        agency=record['agency'], position_title=record['position_title'], catalog_added_date='2026-06-20', filed_at='2026-06-13')
    old['source_rows'][0]['page_number'] = old['transactions'][0]['page_number'] = 2
    empty = base(); empty['meta']['data_cutoff_at'] = CUTOFF
    formal, _ = build_oge_candidate(catalog(record), [old], empty, data_cutoff_at=CUTOFF)
    old.pop('source_rows')  # Actual legacy-reference path; no inferred inventory.
    metadata = {'schema_version':'oge-278t-archive/v1', 'source_id':'oge',
        'document_id':DOCUMENT_ID, 'document_url':record['document_url'], 'sha256':sha,
        'archive_path':relative, 'byte_length':len(raw), **{key:record[key] for key in
            ('filer_name','agency','position_title','catalog_added_date','amended_label','pending_final_oge_disposition')}}
    write(source.with_suffix('.json'), metadata)
    write(review/f'oge/extractions/{DOCUMENT_ID}/{sha}/oge-278t-pdf-v2.json', old)
    source_catalog = catalog(record); raw_catalog = json.dumps(source_catalog, sort_keys=True).encode()
    digest = hashlib.sha256(raw_catalog).hexdigest()
    normalized = evidence/f'oge/catalog/normalized/{digest}.json'; normalized.parent.mkdir(parents=True); normalized.write_bytes(raw_catalog)
    write(evidence/f'oge/catalog/{digest}.json', {'schema_version':'oge-catalog-archive/v1',
        'sha256':digest, 'retrieved_at':CUTOFF})
    write(review/'status/oge.json', {'catalog_sha256':digest})
    observation = saved([old['transactions'][0]['cells']]); observation['source_sha256'] = sha
    request = {'observations':[observation], 'document_page_count':2, 'archived_pdf_path':str(source)}
    return dict(root=root, source=source, sha=sha, evidence=evidence, review=review, old=old,
        record=record, catalog=source_catalog, metadata=metadata, formal=formal, request=request,
        context=_cover_identity_context(old, record))


class CoverConsumptionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def consume(self, data):
        return build_oge_candidate(data['catalog'], [data['old']], data['formal'], data_cutoff_at=CUTOFF,
            saved_observations_by_document={DOCUMENT_ID:data['request']})

    def status(self, data, candidate, audit):
        write(data['review']/'oge/qualifications/current.json', audit)
        return build_oge_status(data['evidence'], data['review'], candidate['transactions'], [])

    def test_original_cover_closes_missing_or_observed_no_slot_page_without_fact_changes(self):
        data = fixture(self.root); before = data['source'].read_bytes()
        for observed in (False, True):
            with self.subTest(observed=observed):
                request = deepcopy(data['request'])
                if observed:
                    request['observations'][0]['pages'].insert(0, {'page_number':1,
                        'column_geometry':'unknown', 'physical_slot_count':0, 'slots':[]})
                selected = {**data, 'request':request}
                candidate, audit = self.consume(selected)
                receipt = audit['reports'][0]['observation_receipt']
                self.assertEqual([p['page_number'] for p in receipt['non_table_page_proofs']], [1])
                self.assertEqual(receipt['unobserved_pages'], [])
                self.assertFalse(receipt['coverage_complete'])
                self.assertEqual(candidate['transactions'], data['formal']['transactions'])
                scopes = self.status(data, candidate, audit)
                record = next(s for s in scopes if s['scope_id']=='oge_direct_278t')['reports'][0]
                self.assertEqual(record['qualification_state'], 'partial')
                self.assertEqual(record['source_observation_coverage']['non_table_pages'], [1])
                self.assertIsNone(record['unique_source_quarantined_row_count'])
                materialized = materialize_oge_278t_observations(data['old'], data['record'],
                    formal_reference_ids=[row['id'] for row in data['formal']['transactions']], **request)
                adapted = adapt_oge_278t_extraction(materialized, data['record'],
                    raster_observations=request['observations'], document_page_count=2, archived_pdf_path=data['source'])
                ledger = adapted['source_report_ledger']
                self.assertEqual(ledger['pages'][0]['coverage'], 'non_table')
                self.assertEqual(ledger['pages'][1]['coverage'], 'unknown')
                self.assertEqual(ledger['logical_row_count'], 1)
                repeated, repeated_audit = build_oge_candidate(data['catalog'], [data['old']], candidate,
                    data_cutoff_at=CUTOFF, saved_observations_by_document={DOCUMENT_ID:request})
                self.assertEqual(repeated, candidate)
                self.assertEqual(repeated_audit['reports'][0]['observation_receipt'], receipt)
        self.assertEqual(data['source'].read_bytes(), before)

    def test_existing_privacy_page_is_in_the_normal_status_partition(self):
        data = fixture(self.root, role='privacy')
        candidate, audit = self.consume(data)
        scopes = self.status(data, candidate, audit)
        record = next(s for s in scopes if s['scope_id']=='oge_direct_278t')['reports'][0]
        self.assertEqual(record['source_observation_coverage']['non_table_pages'], [1])
        self.assertEqual(record['qualification_state'], 'partial')

    def test_parser_slot_and_unresolved_claims_keep_unknown_and_logical_references(self):
        data = fixture(self.root)
        for kind in ('parser', 'slot', 'unresolved'):
            with self.subTest(kind=kind):
                selected = deepcopy(data)
                if kind == 'parser':
                    selected['old']['quarantined'].append({**deepcopy(selected['old']['transactions'][0]),
                        'extraction_id':'oge-278t:'+'e'*24, 'page_number':1, 'reasons':['parser_claim']})
                else:
                    page = deepcopy(selected['request']['observations'][0]['pages'][0]); page['page_number'] = 1
                    if kind == 'unresolved':
                        page['column_geometry'] = 'unknown'; page.pop('column_edges_pdf_points', None)
                    selected['request']['observations'][0]['pages'].insert(0, page)
                materialized = materialize_oge_278t_observations(selected['old'], selected['record'], **selected['request'])
                receipt = materialized['observation_receipt']
                self.assertEqual(receipt['non_table_page_proofs'], [])
                self.assertEqual([p['page_number'] for p in receipt['page_role_conflicts']], [1])
                self.assertFalse(receipt['coverage_complete'])
                adapted = adapt_oge_278t_extraction(materialized, selected['record'],
                    raster_observations=selected['request']['observations'], document_page_count=2,
                    archived_pdf_path=selected['source'])
                self.assertEqual(adapted['source_report_ledger']['pages'][0]['coverage'], 'unknown')
                if kind == 'slot':
                    self.assertTrue(any(ref['page_number']==1 for row in adapted['source_report_ledger']['logical_rows'] for ref in row['slot_refs']))

    def test_saved_shadow_replay_does_not_erase_positive_row_declarations(self):
        data = fixture(self.root)
        report = inspect_pdf(data['source'], cover_identity_context=data['context'])
        self.assertEqual(report['pages'][0]['coverage'], 'non_table_section')
        self.assertEqual(oge_278t_shadow_pages(report, source_id='oge', archived_pdf_path=data['source'],
            cover_identity_context=data['context'])[0]['coverage'], 'non_table')
        self.assertEqual(oge_278t_shadow_pages(report, source_id='oge', archived_pdf_path=data['source'])[0]['coverage'], 'unknown')
        for change in ('bands','unresolved','native_prefix','parser'):
            changed = json.loads(json.dumps(report)); page = changed['pages'][0]
            kwargs = {}
            if change == 'bands':
                page.update(row_coverage='physical_slots_located', candidate_row_bands=[[100.,130.]])
            elif change == 'unresolved':
                page['unresolved_row_regions'] = [[140.,160.]]
            elif change == 'native_prefix':
                page['native_unnumbered_prefix_rows'] = 1
            else:
                kwargs['parser_claimed_pages'] = {1}
            ledger = oge_278t_shadow_pages(changed, source_id='oge', archived_pdf_path=data['source'],
                cover_identity_context=data['context'], **kwargs)
            self.assertEqual(ledger[0]['coverage'], 'unknown')
            if change == 'bands': self.assertEqual(ledger[0]['slots'][0]['bounds'], [100.,130.])
            if change == 'unresolved': self.assertEqual(ledger[0]['unresolved_regions'], [[140.,160.]])

    def test_modified_source_wrong_context_and_forged_receipt_do_not_close_status(self):
        data = fixture(self.root)
        candidate, audit = self.consume(data)
        self.status(data, candidate, audit)
        for change in ('identity','duplicate','overlap','proof','metadata'):
            altered = deepcopy(audit); record = deepcopy(data['record'])
            receipt = altered['reports'][0]['observation_receipt']
            if change == 'identity': record['agency'] = 'Unrelated agency'
            elif change == 'duplicate': receipt['non_table_page_proofs'] *= 2
            elif change == 'overlap': receipt['unobserved_pages'] = [1]
            elif change == 'proof': receipt['non_table_page_proofs'][0]['native_character_count'] = 1
            else:
                meta = {**data['metadata'], 'byte_length':1}; write(data['source'].with_suffix('.json'), meta)
            from unison_snapshot.disclosure_status_oge import _record, _apply_audit
            status = _record(DOCUMENT_ID, data['record']['document_url'], '278t'); status['source_versions']=[data['sha']]
            with self.subTest(change=change), self.assertRaises(ValueError):
                _apply_audit(status, altered['reports'][0], review=data['review'], evidence=data['evidence'], catalog_record=record)
        write(data['source'].with_suffix('.json'), data['metadata'])
        data['old'].update(filed_at=None, evidence_complete=False, document_reasons=['filed_at_invalid'])
        candidate, audit = self.consume(data)
        materialized = materialize_oge_278t_observations(data['old'], data['record'], **data['request'])
        self.assertIsNone(materialized['filed_at'])
        self.assertIn('filed_at_invalid', audit['reports'][0]['document_reasons'])
        self.assertFalse(audit['reports'][0]['observation_receipt']['coverage_complete'])

    def test_normal_saved_cli_fixed_status_builder_reader_and_failure_leave_inputs(self):
        data = fixture(self.root)
        extraction_dir = self.root/'extractions'; write(extraction_dir/f'{DOCUMENT_ID}.json', data['old'])
        write(self.root/'catalog.json', data['catalog'])
        write(self.root/'archive.json', {'schema_version':'oge-278t-archive-batch/v1',
            'catalog_direct_count':1, 'pending_count':0, 'attempted_count':1, 'archived_count':1,
            'failure_count':0, 'failures':[], 'reports':[data['metadata']]})
        write(self.root/'extracted.json', {'schema_version':'oge-278t-extraction-batch/v1',
            'report_count':1, 'extraction_count':1, 'failure_count':0, 'failures':[]})
        portable = {k:v for k,v in data['request'].items() if k != 'archived_pdf_path'}
        write(self.root/'saved.json', {DOCUMENT_ID:portable}); write(self.root/'base.json', data['formal'])
        for args in (('init','-q'), ('config','user.name','Contract'),
                     ('config','user.email','contract@example.invalid'), ('add','.'), ('commit','-qm','fixed evidence contract')):
            subprocess.run(['git','-C',str(data['evidence']),*args], check=True, capture_output=True)
        evidence_commit = subprocess.check_output(['git','-C',str(data['evidence']),'rev-parse','HEAD'], text=True).strip()
        repository = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(repository/'backend/src'), GIT_NO_LAZY_FETCH='1')
        def command(output):
            return [sys.executable, '-m', 'unison_snapshot', 'build-oge-candidate',
                '--catalog', str(self.root/'catalog.json'), '--extractions-dir',str(extraction_dir),
                '--evidence-root',str(data['evidence']), '--saved-observations',str(self.root/'saved.json'),
                '--base',str(self.root/'base.json'), '--data-cutoff-at',CUTOFF,
                '--output',str(output/'candidate.json'), '--audit-output',str(output/'candidate_audit.json')]
        output = self.root/'output'
        result = subprocess.run(command(output), env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        candidate = json.loads((output/'candidate.json').read_bytes())
        audit = json.loads((output/'candidate_audit.json').read_bytes())
        self.assertEqual(candidate['transactions'], data['formal']['transactions'])
        self.assertEqual([p['page_number'] for p in audit['reports'][0]['observation_receipt']['non_table_page_proofs']], [1])
        self.status(data, candidate, audit)
        # The actual status CLI insists on clean, fixed source trees.
        for tree in (data['review'],):
            for args in (('init','-q'), ('config','user.name','Contract'),
                         ('config','user.email','contract@example.invalid'), ('add','.'), ('commit','-qm','fixed source contract')):
                subprocess.run(['git','-C',str(tree),*args], check=True, capture_output=True)
        status_path = self.root/'status-input.json'
        status_command = [sys.executable,str(repository/'backend/src/unison_snapshot/commands/build_disclosure_status.py'),
            '--evidence-root',str(data['evidence']), '--review-root',str(data['review']),
            '--input',str(output/'candidate.json'), '--code-commit','b'*40, '--previous-main-commit','a'*40,
            '--output',str(status_path)]
        checked = subprocess.run(status_command, env=env, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        status_input = json.loads(status_path.read_bytes())
        from unison_snapshot.builder import build
        from unison_snapshot.disclosure_status import attach_disclosure_status
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        bundle = build(candidate, generated_at='2026-06-20T00:02:00Z', allow_production=True)
        bundle = attach_disclosure_status(bundle, status_input['scopes'], transactions=candidate['transactions'],
            provenance=status_input['provenance'])
        public = PublicSnapshotRepository('contract','data',transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'], candidate['transactions'])
        records = public.disclosure_status(public.resolve(), include_records=True)
        self.assertIsNotNone(records)
        # Repeating source replay yields exactly the same candidate/audit bytes.
        second = self.root/'repeat'
        repeated = subprocess.run(command(second), env=env, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        for name in ('candidate.json','candidate_audit.json'):
            self.assertEqual((output/name).read_bytes(), (second/name).read_bytes())
        before = data['source'].read_bytes(), (output/'candidate.json').read_bytes(), (output/'candidate_audit.json').read_bytes()
        bad = deepcopy(portable); bad['observations'][0]['source_sha256'] = '0'*64
        write(self.root/'saved.json', {DOCUMENT_ID:bad})
        failed = self.root/'failed'
        rejected = subprocess.run(command(failed), env=env, capture_output=True, text=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(failed.exists())
        self.assertEqual(before, (data['source'].read_bytes(), (output/'candidate.json').read_bytes(), (output/'candidate_audit.json').read_bytes()))

    def test_source_conflict_does_not_grant_new_parser_or_saved_facts(self):
        data = fixture(self.root, role='privacy')
        page = deepcopy(data['request']['observations'][0]['pages'][0]); page['page_number'] = 1
        data['request']['observations'][0]['pages'].insert(0, page)
        candidate, audit = self.consume(data)
        self.assertEqual(candidate['transactions'], data['formal']['transactions'])
        quarantined = audit['reports'][0]['quarantined']
        self.assertTrue(any('source_page_role_conflicts_with_observed_rows' in row.get('reasons', []) for row in quarantined))
        scopes = self.status(data, candidate, audit)
        record = next(s for s in scopes if s['scope_id']=='oge_direct_278t')['reports'][0]
        self.assertEqual(record['source_observation_coverage']['page_role_conflict_pages'], [1])
        self.assertEqual(record['qualification_state'], 'partial')
        # An uncommitted parser cache row on the same source page gets no old-
        # fact protection merely because it has an extraction ID.
        parser = deepcopy(data['old']); parser['transactions'][0]['page_number'] = 1
        parser['source_rows'] = [{'page_number':1, 'cells':parser['transactions'][0]['cells']}]
        materialized = materialize_oge_278t_observations(parser, data['record'], **data['request'])
        self.assertFalse(any(row.get('page_number')==1 for row in materialized['transactions']))
        self.assertIn(parser['transactions'][0]['extraction_id'], {row.get('extraction_id') for row in materialized['quarantined']})
        empty = base(); empty['meta']['data_cutoff_at'] = CUTOFF
        existing, _ = build_oge_candidate(data['catalog'], [parser], empty, data_cutoff_at=CUTOFF)
        same_page_request = deepcopy(data['request'])
        same_page_request['observations'][0]['pages'] = [page]
        preserved, preserved_audit = build_oge_candidate(data['catalog'], [parser], existing, data_cutoff_at=CUTOFF,
            saved_observations_by_document={DOCUMENT_ID:same_page_request})
        self.assertEqual(preserved['transactions'], existing['transactions'])
        self.assertTrue(preserved_audit['reports'][0]['observation_receipt']['page_role_conflicts'])
        fresh, fresh_audit = build_oge_candidate(data['catalog'], [parser], empty, data_cutoff_at=CUTOFF,
            saved_observations_by_document={DOCUMENT_ID:same_page_request})
        self.assertNotIn(parser['transactions'][0]['extraction_id'], {row['id'] for row in fresh['transactions']})
        self.assertIn(parser['transactions'][0]['extraction_id'], {row.get('extraction_id') for row in fresh_audit['reports'][0]['quarantined']})

