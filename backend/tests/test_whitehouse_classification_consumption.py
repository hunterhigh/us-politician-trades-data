"""Native source -> retained consumer -> normal CLI; no new OCR or network."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_whitehouse_owner_correction import fixture, encoded
from unison_snapshot.commands import build_whitehouse_278t_candidate as cli
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.whitehouse_278t_audit import audit_whitehouse_278t
from unison_snapshot.whitehouse_278t_candidate import build_whitehouse_278t_review_candidate
from unison_snapshot.whitehouse_native_correction import POLICY_VERSION, prepare_native_classification_sources


class NativeClassificationConsumptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = fixture()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.review, self.evidence = self.root/'review', self.root/'evidence'
        old, normalized, review, evidence, base = deepcopy(self.data)
        self.old, self.base = old, base
        for folder, files in ((self.review, review), (self.evidence, evidence)):
            for relative, raw in files.items():
                path = folder/relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        self.candidate, self.audit = self.root/'candidate.json', self.root/'audit.json'
        self.candidate.write_bytes(encoded(base))

    def run_cli(self, **extra):
        options = dict(oge_candidate=self.candidate, candidate_out=self.candidate,
            audit_out=self.audit, review_root=self.review, evidence_root=self.evidence,
            expected_report_count=1)
        options.update(extra)
        return cli.run(**options)

    def read_candidate(self):
        return json.loads(self.candidate.read_bytes())

    def add_native_alias(self, *, extra_legacy=False):
        from unison_snapshot.whitehouse_disclosures import parse_disclosure_index
        from unison_snapshot.whitehouse_278t import parse_generic_whitehouse_278t_pdf
        url = self.old['source_url'].replace('Example-PTR', 'Other-location-PTR')
        document = 'wh-url:'+hashlib.sha256(url.encode()).hexdigest()[:24]
        pdf = next(self.evidence.rglob('*.pdf')); sha = pdf.stem
        original_meta = json.loads(pdf.with_suffix('.json').read_bytes())
        html_path = self.evidence/f"whitehouse/disclosures/pages/{original_meta['page_sha256']}.html"
        html = html_path.read_bytes().replace(b'</details>',
            b'</details>', 1).replace(b'</a></details>',
            f'</a><a href="{url}">{original_meta["link_label"]}</a></details>'.encode())
        index = parse_disclosure_index(html)
        new_html = self.evidence/f"whitehouse/disclosures/pages/{index['page_sha256']}.html"
        new_html.write_bytes(html)
        original_meta['page_sha256'] = index['page_sha256']
        pdf.with_suffix('.json').write_bytes(encoded(original_meta))
        alias_pdf = self.evidence/f'whitehouse/disclosures/reports/{document[7:]}/{sha}.pdf'
        alias_pdf.parent.mkdir(parents=True); alias_pdf.write_bytes(pdf.read_bytes())
        entry = next(row for row in index['reports'] if row['source_document_id'] == document)
        alias_meta = {**original_meta, **entry, 'document_id':document,
            'archive_path':alias_pdf.relative_to(self.evidence).as_posix()}
        alias_pdf.with_suffix('.json').write_bytes(encoded(alias_meta))
        old_legacy_path = next(self.review.rglob('whitehouse-278t-hybrid-geometry-v2.json'))
        legacy = json.loads(old_legacy_path.read_bytes()); legacy.update(document_id=document, source_url=url)
        if extra_legacy:
            legacy['unused_but_preserved'] = 'different source observation'
        native = parse_generic_whitehouse_278t_pdf(alias_pdf, document_id=document, source_url=url,
            source_sha256=sha, filer_name=alias_meta['filer_name_from_label'],
            amended_label=alias_meta['link_label'], legacy_extraction=legacy)
        folder = self.review/f'whitehouse/extractions/{document[7:]}/{sha}'; folder.mkdir(parents=True)
        (folder/'whitehouse-278t-hybrid-geometry-v2.json').write_bytes(encoded(legacy))
        (folder/'whitehouse-278t-native-roles-v5.json').write_bytes(encoded(native))
        coverage_path = self.review/'whitehouse/coverage-current.json'
        coverage = json.loads(coverage_path.read_bytes()); row = deepcopy(coverage['reports'][0])
        row.update(document_id=document, document_url=url,
            extraction_path=(folder/'whitehouse-278t-native-roles-v5.json').relative_to(self.review).as_posix())
        coverage['reports'].append(row); coverage.update(report_link_count=2,
            index_page_sha256=index['page_sha256'], counts_by_review_state={'extracted_review_only':2})
        coverage_path.write_bytes(encoded(coverage))
        (self.review/'whitehouse/disclosures/index-current.json').write_bytes(encoded(index))

    def test_both_owner_predecessors_keep_id_and_other_fields_and_repeat_bytes(self):
        for owner in ('Self', 'Undisclosed'):
            with self.subTest(owner=owner):
                base = deepcopy(self.base); base['transactions'][0]['owner'] = owner
                self.candidate.write_bytes(encoded(base)); self.audit.unlink(missing_ok=True)
                self.assertFalse(self.run_cli()['idempotent'])
                result = self.read_candidate()
                self.assertEqual(result['transactions'], [{**self.old, 'owner':'Undisclosed', 'instrument_type':'Stock'}])
                for field in ('people', 'reported_holdings', 'security_market_data'):
                    self.assertEqual(result[field], base[field])
                changes = json.loads(self.audit.read_bytes())['source_field_corrections']
                self.assertEqual({row['field'] for row in changes},
                    {'instrument_type', 'owner'} if owner == 'Self' else {'instrument_type'})
                before = self.candidate.read_bytes(), self.audit.read_bytes()
                self.assertTrue(self.run_cli()['idempotent'])
                self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_old_pair_without_policy_recomputes_once_and_same_scope_repeats(self):
        self.run_cli()
        current = self.read_candidate(); current['transactions'][0]['instrument_type'] = 'Bond'
        self.candidate.write_bytes(cli._json_bytes(current))
        audit = json.loads(self.audit.read_bytes())
        audit['candidate_sha256'] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        for key in list(audit['review_input_sha256']):
            if key.startswith('native_endnote_classification_'):
                del audit['review_input_sha256'][key]
        self.audit.write_bytes(cli._json_bytes(audit))
        self.assertFalse(self.run_cli()['idempotent'])
        self.assertEqual(self.read_candidate()['transactions'][0]['instrument_type'], 'Stock')
        first = self.candidate.read_bytes(), self.audit.read_bytes()
        self.assertTrue(self.run_cli()['idempotent'])
        self.assertEqual(first, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_correction_data_uses_source_proof_and_invalidates_completed_cache(self):
        from unison_snapshot import reviewed_fact_corrections as reviewed
        from unison_snapshot.codec import encode, digest
        report = json.loads(next(self.review.rglob('whitehouse-278t-native-roles-v5.json')).read_bytes())
        row = report['transactions'][0]; old = self.base['transactions'][0]
        final = {**old, 'owner': 'Undisclosed', 'instrument_type': 'Stock'}
        correction = {'schema_version': 'source-correction/v1', 'profile': reviewed.PROFILE,
            'source': 'oge', 'document_id': report['document_id'], 'source_url': report['source_url'],
            'source_sha256': report['source_sha256'], 'reviewed_at': '2026-10-28',
            'assertions': {'row_inventory': [old['id']],
                'source_rows': {old['id']: {k: row[k] for k in ('page_number','row_number','cells')}},
                'predecessor_sha256': {old['id']: [digest(encode(old))]},
                'output_sha256': {old['id']: digest(encode(final))}},
            'actions': [{'op': 'set_field', 'row': old['id'], 'field': 'instrument_type',
                'expect': 'Bond', 'value': 'Stock', 'evidence': {'page': row['page_number']}}]}
        rules = self.root/'corrections';rules.mkdir()
        path = rules/(report['document_id'][7:]+'.json')
        path.write_bytes(encode(correction))
        with patch.object(reviewed, 'ROOT', rules):
            self.run_cli()
            self.assertEqual(self.read_candidate()['transactions'], [final])
            stable = self.candidate.read_bytes(), self.audit.read_bytes()
            self.assertTrue(self.run_cli()['idempotent'])
            correction['assertions']['output_sha256'][old['id']] = 'f'*64
            path.write_bytes(encode(correction))
            with self.assertRaisesRegex(ValueError, 'verified source output'):
                self.run_cli()
            self.assertEqual(stable, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_dependency_mutation_and_missing_roots_fail_before_both_outputs(self):
        self.run_cli(); before = self.candidate.read_bytes(), self.audit.read_bytes()
        with self.assertRaisesRegex(ValueError, 'roots'):
            self.run_cli(evidence_root=None)
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))
        path = next(self.evidence.rglob('*.pdf')); raw = path.read_bytes(); path.write_bytes(raw+b'changed')
        with self.assertRaises((ValueError, OgeCatalogError)):
            self.run_cli()
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_pure_old_call_does_not_gain_new_fact_permission(self):
        extractions, annual, coverage, directory, people, *_ = cli._verify_review(self.review, expected_report_count=1)
        eligibility = audit_whitehouse_278t(extractions, coverage, self.base, annual, directory, people)
        result, _ = build_whitehouse_278t_review_candidate(self.base, extractions, eligibility, coverage,
            data_cutoff_at=self.base['meta']['data_cutoff_at'], directory_index=directory)
        self.assertEqual(result['transactions'][0], {**self.old, 'owner':'Undisclosed'})
        sources = prepare_native_classification_sources(extractions, evidence_root=self.evidence, review_root=self.review,
            previous_transactions=self.base['transactions'])
        proved, _ = build_whitehouse_278t_review_candidate(self.base, extractions, eligibility, coverage,
            data_cutoff_at=self.base['meta']['data_cutoff_at'], directory_index=directory,
            native_classification_sources=sources)
        self.assertEqual(proved['transactions'][0]['instrument_type'], 'Stock')

    def test_exact_mode_single_native_source_and_nested_family_replay(self):
        self.run_cli(exact_source_copies=True)
        self.assertEqual(self.read_candidate()['transactions'][0]['instrument_type'], 'Stock')
        self.assertTrue(self.run_cli(exact_source_copies=True)['idempotent'])
        from unison_snapshot.oge_cross_source_replacements import _bound_whitehouse_candidate
        extractions, annual, coverage, directory, people, *_ = cli._verify_review(self.review, expected_report_count=1)
        inputs = {'base': self.base, 'extractions': extractions, 'oge_whitehouse_coverage': coverage,
            'directory_index': directory, 'annual_extractions': annual, 'annual_identity_people':people}
        old, _, _ = _bound_whitehouse_candidate(inputs, self.evidence, self.review)
        self.assertEqual(old['transactions'][0]['instrument_type'], 'Bond')
        inputs['native_classification_policy_version'] = POLICY_VERSION
        new, _, _ = _bound_whitehouse_candidate(inputs, self.evidence, self.review)
        self.assertEqual(new['transactions'][0]['instrument_type'], 'Stock')
        self.assertIsInstance(inputs['native_classification_policy_version'], str)
        inputs['native_classification_policy_version'] = 'invented'
        with self.assertRaisesRegex(OgeCatalogError, 'policy'):
            _bound_whitehouse_candidate(inputs, self.evidence, self.review)

    def test_actual_command_and_override_paths(self):
        coverage = self.review/'whitehouse/coverage-current.json'
        status = self.review/'whitehouse/extraction-status.json'
        coverage_override = coverage.with_name('coverage-selected.json'); coverage_override.write_bytes(coverage.read_bytes())
        status_override = status.with_name('status-selected.json'); status_override.write_bytes(status.read_bytes())
        root = Path(__file__).resolve().parents[2]
        command = [sys.executable, str(root/'backend/src/unison_snapshot/commands/build_whitehouse_278t_candidate.py'),
            '--oge-candidate', str(self.candidate), '--candidate-out', str(self.candidate),
            '--audit-out', str(self.audit), '--review-root', str(self.review), '--evidence-root', str(self.evidence),
            '--coverage-input', str(coverage_override), '--extraction-status-input', str(status_override)]
        result = subprocess.run(command, env=dict(os.environ, PYTHONPATH=str(root/'backend/src')),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_candidate()['transactions'][0]['instrument_type'], 'Stock')
        before = self.candidate.read_bytes(), self.audit.read_bytes()
        result = subprocess.run(command, env=dict(os.environ, PYTHONPATH=str(root/'backend/src')),
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['idempotent'])
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_real_native_alias_is_routed_once_and_wrong_predecessor_cannot_hide_in_retained(self):
        self.add_native_alias()
        base = deepcopy(self.base); base['transactions'][0]['owner'] = 'Undisclosed'
        self.candidate.write_bytes(encoded(base))
        self.run_cli(expected_report_count=2, exact_source_copies=True)
        result = self.read_candidate(); audit = json.loads(self.audit.read_bytes())
        self.assertEqual(result['transactions'], [{**self.old,'owner':'Undisclosed','instrument_type':'Stock'}])
        self.assertEqual(audit['retained_existing_transaction_count'], 1)
        self.assertEqual(audit['exact_source_alias_row_count'], 1)
        self.assertEqual(audit['source_row_count'], 2)
        self.assertEqual(len(audit['exact_source_aliases']), 1)
        before = self.candidate.read_bytes(), self.audit.read_bytes()
        self.assertTrue(self.run_cli(expected_report_count=2, exact_source_copies=True)['idempotent'])
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))
        base['transactions'][0]['instrument_type'] = 'ETF'
        self.candidate.write_bytes(encoded(base)); before = self.candidate.read_bytes(), self.audit.read_bytes()
        with self.assertRaisesRegex(ValueError, 'predecessor'):
            self.run_cli(expected_report_count=2, exact_source_copies=True)
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_alias_requires_full_legacy_equivalence_and_sources(self):
        from unison_snapshot.whitehouse_278t_audit import resolve_exact_source_reports
        self.add_native_alias(extra_legacy=True)
        extractions, _, coverage, *_ = cli._verify_review(self.review, expected_report_count=2)
        sources = prepare_native_classification_sources(extractions, evidence_root=self.evidence, review_root=self.review,
            previous_transactions=self.base['transactions'])
        self.assertEqual(resolve_exact_source_reports(extractions, coverage,
            existing_transactions=self.base['transactions'], native_classification_sources=sources)[1], [])
        self.assertEqual(resolve_exact_source_reports(extractions, coverage,
            existing_transactions=self.base['transactions'])[1], [])

    def test_self_signed_current_policy_cannot_skip_pending_or_wrong_projection(self):
        self.run_cli()
        current = self.read_candidate(); current['transactions'][0]['instrument_type'] = 'Bond'
        self.candidate.write_bytes(cli._json_bytes(current))
        audit = json.loads(self.audit.read_bytes()); audit['candidate_sha256'] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        self.audit.write_bytes(cli._json_bytes(audit))
        self.assertFalse(self.run_cli()['idempotent'])
        self.assertEqual(self.read_candidate()['transactions'][0]['instrument_type'], 'Stock')
        current = self.read_candidate(); current['transactions'][0]['invented_optional'] = None
        self.candidate.write_bytes(cli._json_bytes(current))
        audit = json.loads(self.audit.read_bytes()); audit['candidate_sha256'] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        self.audit.write_bytes(cli._json_bytes(audit)); before = self.candidate.read_bytes(), self.audit.read_bytes()
        with self.assertRaisesRegex(ValueError, 'predecessor'):
            self.run_cli()
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_self_signed_prior_cannot_hide_by_becoming_ineligible_normal_or_exact(self):
        for exact in (False, True):
            for field, value in [('amount_low',1002), ('owner','Spouse'), ('asset_name','Changed asset')]:
                with self.subTest(exact=exact, field=field):
                    self.candidate.write_bytes(encoded(self.base)); self.audit.unlink(missing_ok=True)
                    self.run_cli(exact_source_copies=exact)
                    current = self.read_candidate(); current['transactions'][0][field] = value
                    self.candidate.write_bytes(cli._json_bytes(current))
                    audit = json.loads(self.audit.read_bytes())
                    audit['candidate_sha256'] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
                    self.audit.write_bytes(cli._json_bytes(audit)); before = self.candidate.read_bytes(), self.audit.read_bytes()
                    with self.assertRaises((ValueError, OgeCatalogError)):
                        self.run_cli(exact_source_copies=exact)
                    self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_erased_raw_scope_cannot_self_sign_an_empty_policy_cache(self):
        from unison_snapshot.whitehouse_native_correction import NativeClassificationSources
        self.run_cli()
        native_path = next(self.review.rglob('*native-roles-v5.json'))
        native = json.loads(native_path.read_bytes()); native['source_rows'] = []
        native_path.write_bytes(encoded(native))
        audit = json.loads(self.audit.read_bytes())
        provenance = cli._verify_review(self.review, expected_report_count=1)[5]
        provenance.update(NativeClassificationSources().provenance())
        audit['review_input_sha256'] = provenance
        audit['candidate_sha256'] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        self.audit.write_bytes(cli._json_bytes(audit)); before = self.candidate.read_bytes(), self.audit.read_bytes()
        with self.assertRaises((ValueError, OgeCatalogError)):
            self.run_cli()
        self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_fresh_native_endnote_without_legacy_or_roots_keeps_first_publication(self):
        from unison_snapshot.whitehouse_278t import parse_generic_whitehouse_278t_pdf
        pdf = next(self.evidence.rglob('*.pdf')); meta = json.loads(pdf.with_suffix('.json').read_bytes())
        native = parse_generic_whitehouse_278t_pdf(pdf, document_id=meta['document_id'],
            source_url=meta['document_url'], source_sha256=pdf.stem,
            filer_name=meta['filer_name_from_label'], amended_label=meta['link_label'])
        native_path = next(self.review.rglob('whitehouse-278t-native-roles-v5.json'))
        native_path.write_bytes(encoded(native))
        next(self.review.rglob('whitehouse-278t-hybrid-geometry-v2.json')).unlink()
        self.base['transactions'] = []; self.base['people'] = []
        self.candidate.write_bytes(encoded(self.base))
        for evidence in (None, self.evidence):
            with self.subTest(evidence_root=evidence):
                self.candidate.write_bytes(encoded(self.base)); self.audit.unlink(missing_ok=True)
                self.run_cli(evidence_root=evidence)
                self.assertEqual(self.read_candidate()['transactions'][0]['instrument_type'], 'Stock')
                before = self.candidate.read_bytes(), self.audit.read_bytes()
                self.assertTrue(self.run_cli(evidence_root=evidence)['idempotent'])
                self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))

    def test_missing_legacy_for_an_actual_historical_id_does_not_become_fresh(self):
        next(self.review.rglob('whitehouse-278t-hybrid-geometry-v2.json')).unlink()
        before = self.candidate.read_bytes()
        with self.assertRaises((ValueError, OSError)):
            self.run_cli()
        self.assertEqual(self.candidate.read_bytes(), before)
        self.assertFalse(self.audit.exists())

    def test_builder_and_public_reader_keep_classified_stable_fact(self):
        from unison_snapshot.builder import build
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        self.run_cli()
        result = self.read_candidate()
        files = build(result, generated_at='2026-06-20T00:01:00Z', allow_production=True).files
        public = PublicSnapshotRepository('contract', 'data', transport=FakeTransport(files)).fetch().snapshot
        self.assertEqual(public['transactions'], result['transactions'])
        self.assertEqual(public['transactions'][0]['instrument_type'], 'Stock')
        self.assertEqual(public['transactions'][0]['id'], self.old['id'])

    def test_exhibit_application_verify_and_replay_rebuild_new_policy_from_real_roots(self):
        from unittest.mock import patch
        import pdfplumber
        from test_oge_whitehouse_replacements import cross_fixture, DOCUMENT_ID
        from test_oge_annual import _Document, _Page
        from unison_snapshot import oge_cross_source_replacements as cross
        d = cross_fixture(self.root/'exhibit')
        for source, target in ((d['evidence'], self.evidence), (d['review'], self.review)):
            for path in source.rglob('*'):
                if path.is_file():
                    out = target/path.relative_to(source); out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(path.read_bytes())
        native_path = next(path for path in self.review.rglob('*native-roles-v5.json'))
        native = json.loads(native_path.read_bytes())
        directory = json.loads((self.review/'whitehouse/disclosures/index-current.json').read_bytes())
        coverage = json.loads((self.review/'oge/whitehouse/coverage-current.json').read_bytes())
        inputs = {'base':self.base, 'extractions':[native, d['wh']], 'directory_index':directory,
            'oge_whitehouse_coverage':coverage, 'native_classification_policy_version':POLICY_VERSION}
        # The real CLI permits a selected v5 inventory while current still
        # selects v2. Persist only these bounded relative source references.
        current = self.review/'whitehouse/coverage-current.json'
        selected = current.with_name('coverage-selected.json'); selected.write_bytes(current.read_bytes())
        selected_inventory = json.loads(selected.read_bytes())
        selected_inventory['reports'].append({'document_id':d['wh']['document_id'],
            'document_url':d['wh']['source_url'], 'document_type_from_label':'278t',
            'archive_sha256_versions':[d['wh']['source_sha256']], 'review_state':'extracted_review_only',
            'extraction_parser_version':d['wh']['parser_version']})
        selected_inventory.update(report_link_count=2, counts_by_review_state={'extracted_review_only':2})
        selected.write_bytes(encoded(selected_inventory))
        old_inventory = json.loads(current.read_bytes())
        old_inventory['reports'][0].update(extraction_parser_version='whitehouse-278t-hybrid-geometry/v2',
            extraction_path=native_path.with_name('whitehouse-278t-hybrid-geometry-v2.json').relative_to(self.review).as_posix())
        current.write_bytes(encoded(old_inventory))
        current_status = self.review/'whitehouse/extraction-status.json'
        selected_status = current_status.with_name('status-selected.json'); selected_status.write_bytes(current_status.read_bytes())
        inputs['native_classification_selection'] = {
            'coverage_input':selected.relative_to(self.review).as_posix(),
            'extraction_status_input':selected_status.relative_to(self.review).as_posix()}
        real_open = pdfplumber.open
        from unison_snapshot.whitehouse_278t import plan_whitehouse_278t_archive as real_plan
        def source_plan(metadata, evidence_root, *, generic=False):
            if metadata['sha256'] == d['wh']['source_sha256']:
                # This pre-existing EXHIBIT fixture supplies offline native
                # readings. It does not exercise the new mode inventory.
                return {'parser_version': d['wh']['parser_version'], 'partition_plan': None}
            return real_plan(metadata, evidence_root, generic=generic)
        def opened(path, *args, **kwargs):
            # Fixed OGE readings exercise the old EXHIBIT consumer only. The
            # new native correction always reads and parses its real PDF bytes.
            if (hasattr(path, 'getvalue') and hashlib.sha256(path.getvalue()).hexdigest() == native['source_sha256'] or
                    not hasattr(path, 'getvalue') and Path(path).stem == native['source_sha256']):
                return real_open(path, *args, **kwargs)
            return _Document([_Page('', [d['raw']])])
        with patch('pdfplumber.open', side_effect=opened), \
                patch('unison_snapshot.whitehouse_278t.plan_whitehouse_278t_archive', side_effect=source_plan), \
                patch('unison_snapshot.oge_278t_page_shadow.observe_native_exhibit_page', side_effect=d['native']), \
                patch('unison_snapshot.oge_reports._extract_borderless_transaction_tables', return_value=[]):
            candidate, _, _ = cross._bound_whitehouse_candidate(inputs, self.evidence, self.review)
            saved = {DOCUMENT_ID:d['inputs']}
            result, proof = cross.apply_cross_source_native_replacements(candidate, saved, inputs, self.evidence, self.review)
            self.assertEqual(len(proof['applications']), 1)
            self.assertEqual(next(row for row in result['transactions'] if row['id'] == self.old['id'])['instrument_type'], 'Stock')
            self.assertEqual(proof['context']['wh_inputs']['native_classification_policy_version'], POLICY_VERSION)
            json.dumps(proof)  # No runtime Sources object or absolute root is serialized.
            self.assertEqual(cross.verify_cross_source_native_applications(proof, self.evidence, self.review), (result, proof))
            self.assertEqual(cross.replay_cross_source_context(proof['context'], self.evidence, self.review), (result, proof))
            saved_path = self.root/'saved-exhibit.json'; saved_path.write_bytes(encoded(saved))
            self.run_cli(expected_report_count=2, coverage_input=selected, extraction_status_input=selected_status,
                saved_exhibit_observations=saved_path)
            self.assertEqual(self.read_candidate(), result)
            normal_proof = json.loads(self.audit.read_bytes())['native_cross_source_applications']
            self.assertEqual(normal_proof['context']['wh_inputs']['native_classification_selection'],
                inputs['native_classification_selection'])
            before = self.candidate.read_bytes(), self.audit.read_bytes()
            self.assertTrue(self.run_cli(expected_report_count=2, coverage_input=selected,
                extraction_status_input=selected_status, saved_exhibit_observations=saved_path)['idempotent'])
            self.assertEqual(before, (self.candidate.read_bytes(), self.audit.read_bytes()))
            legacy_inputs = deepcopy(inputs)
            legacy_inputs.pop('native_classification_policy_version')
            legacy_inputs.pop('native_classification_selection')
            legacy_candidate, _, _ = cross._bound_whitehouse_candidate(legacy_inputs, self.evidence, self.review)
            legacy_result, legacy_proof = cross.apply_cross_source_native_replacements(
                legacy_candidate, saved, legacy_inputs, self.evidence, self.review)
            self.assertEqual(next(row for row in legacy_result['transactions'] if row['id'] == self.old['id'])['instrument_type'], 'Bond')
            self.candidate.write_bytes(cli._json_bytes(legacy_result))
            old_audit = json.loads(self.audit.read_bytes())
            old_audit['candidate_sha256'] = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
            old_audit['native_cross_source_applications'] = legacy_proof
            for key in list(old_audit['review_input_sha256']):
                if key.startswith('native_endnote_classification_'):
                    del old_audit['review_input_sha256'][key]
            self.audit.write_bytes(cli._json_bytes(old_audit))
            self.assertFalse(self.run_cli(expected_report_count=2, coverage_input=selected,
                extraction_status_input=selected_status, saved_exhibit_observations=saved_path)['idempotent'])
            self.assertEqual(self.read_candidate(), result)
            upgraded = self.candidate.read_bytes(), self.audit.read_bytes()
            self.assertTrue(self.run_cli(expected_report_count=2, coverage_input=selected,
                extraction_status_input=selected_status, saved_exhibit_observations=saved_path)['idempotent'])
            self.assertEqual(upgraded, (self.candidate.read_bytes(), self.audit.read_bytes()))
            original = deepcopy(result)
            for relative in ('../outside.json', '/absolute.json', 'C:/outside.json', 'whitehouse/missing.json'):
                bad = deepcopy(proof); bad['context']['wh_inputs']['native_classification_selection']['coverage_input'] = relative
                with self.subTest(relative=relative), self.assertRaises((ValueError, OSError, OgeCatalogError)):
                    cross.verify_cross_source_native_applications(bad, self.evidence, self.review)
                self.assertEqual(result, original)
            selected.write_bytes(b'{"reports": []}')
            with self.assertRaises((ValueError, OgeCatalogError)):
                cross.replay_cross_source_context(proof['context'], self.evidence, self.review)
            self.assertEqual(result, original)
