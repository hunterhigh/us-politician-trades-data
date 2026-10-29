"""Source availability isolation never admits invalid or incoherent data."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from unison_snapshot import __main__ as cli
from unison_snapshot.senate import HOME_URL, SenateEfdError, SenateHttpError, SenateTransportError
from tests.test_disclosure_candidate import candidate
from tests import test_senate_production_recovery as recovery

ROOT, step_script = recovery.ROOT, recovery.step_script


def invoke(args):
    with patch.object(sys, 'argv', ['snapshot', *args]), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        try:
            cli.main()
        except SystemExit as error:
            return error.code
    return 0


class SourceFailureCliTests(unittest.TestCase):
    def test_only_known_network_outcomes_are_isolated_and_old_data_is_preserved(self):
        for code, expected in ((403, 'blocked'), (429, 'unavailable'),
                               (503, 'unavailable'), (404, None)):
            for isolate in (False, True):
                with self.subTest(code=code, isolate=isolate), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    output, attempt = root / 'discovery.json', root / 'attempt.json'
                    output.write_bytes(b'prior complete discovery')
                    args = ['discover-senate-efd', '--archive', str(root / 'evidence'),
                            '--output', str(output), '--submitted-start-date', '2026-01-01',
                            '--attempt-output', str(attempt)]
                    if isolate:
                        args.append('--isolate-source-unavailable')
                    error = SenateHttpError(code, Request(HOME_URL), edge_denied=code == 403)
                    with patch.dict(os.environ, {'SENATE_EFD_COLLECTION_ENABLED': 'true',
                            'SENATE_EFD_TERMS_ACKNOWLEDGED': 'true'}), patch.object(
                            cli, 'discover_ptrs', side_effect=error):
                        result = invoke(args)
                    if isolate and expected:
                        self.assertEqual(result, 0)
                        status = json.loads(attempt.read_text())
                        self.assertEqual(status['status'], expected)
                        self.assertFalse(status['available'])
                        self.assertEqual(status['http_status'], code)
                    else:
                        self.assertEqual(result, 2)
                        self.assertFalse(attempt.exists())
                    self.assertEqual(output.read_bytes(), b'prior complete discovery')
                    self.assertFalse((root / 'evidence').exists())

    def test_transport_error_is_typed_but_validation_and_missing_authorization_still_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = ['discover-senate-efd', '--archive', str(root / 'evidence'),
                    '--output', str(root / 'discovery.json'), '--submitted-start-date', '2026-01-01',
                    '--isolate-source-unavailable', '--attempt-output', str(root / 'attempt.json')]
            with patch.dict(os.environ, {'SENATE_EFD_COLLECTION_ENABLED': 'true',
                    'SENATE_EFD_TERMS_ACKNOWLEDGED': 'true'}), patch.object(
                    cli, 'discover_ptrs', side_effect=SenateTransportError(Request(HOME_URL))):
                self.assertEqual(invoke(args), 0)
            self.assertEqual(json.loads((root / 'attempt.json').read_text())['error_code'],
                             'network_unavailable')
            with patch.object(cli, 'discover_ptrs', side_effect=SenateEfdError('Invalid source rows')):
                self.assertEqual(invoke(args), 2)
            with patch.dict(os.environ, {'SENATE_EFD_COLLECTION_ENABLED': 'true',
                                         'SENATE_EFD_TERMS_ACKNOWLEDGED': 'false'}):
                self.assertEqual(invoke(args), 2)
            self.assertFalse((root / 'discovery.json').exists())

    def test_deferred_assembly_keeps_prior_outputs_and_default_remains_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = []
            values = {}
            for source, cutoff in (('house_clerk', '2026-10-27T00:00:00Z'),
                                   ('senate_efd', '2026-10-23T00:00:00Z')):
                value = candidate(source, source + ':person', source + ':fact')
                host = 'disclosures-clerk.house.gov' if source == 'house_clerk' else 'efdsearch.senate.gov'
                value['transactions'][0]['source_url'] = 'https://' + host + '/synthetic-fixture'
                value['meta']['data_cutoff_at'] = cutoff
                value['source_health'][0]['data_cutoff_at'] = cutoff
                target = root / (source + '.json')
                target.write_text(json.dumps(value))
                values[source] = value
                sources += ['--source', source + '=' + str(target)]
            out, audit, html, status = [root / name for name in
                                       ('candidate.json', 'audit.json', 'page.html', 'attempt.json')]
            for file in (out, audit, html):
                file.write_bytes(b'prior immutable output')
            args = ['build-disclosure-candidate', '--base', str(ROOT / 'backend/data/empty-disclosure.json'),
                    *sources, '--harmonize-cutoffs', '--output', str(out),
                    '--audit-output', str(audit), '--html-output', str(html),
                    '--assembly-status-output', str(status)]
            self.assertEqual(invoke(args), 2)
            self.assertFalse(status.exists())
            self.assertEqual(invoke([*args, '--defer-stale-sources']), 0)
            self.assertEqual(json.loads(status.read_text())['status'], 'deferred')
            for file in (out, audit, html):
                self.assertEqual(file.read_bytes(), b'prior immutable output')
            # Even with stale cutoffs, malformed facts and cross-source ID
            # collisions remain hard errors instead of being deferred.
            for mode in ('bad_amount', 'duplicate_fact', 'bad_source',
                         'unknown_person', 'bad_authority', 'bad_structure', 'bad_date'):
                invalid = deepcopy(values['senate_efd'])
                if mode == 'bad_amount':
                    invalid['transactions'][0]['amount_high'] = -10
                elif mode == 'duplicate_fact':
                    invalid['transactions'][0]['id'] = values['house_clerk']['transactions'][0]['id']
                elif mode == 'bad_source':
                    invalid['transactions'][0]['source_url'] = 'https://untrusted.example/report'
                elif mode == 'unknown_person':
                    invalid['transactions'][0]['person_id'] = 'unknown'
                elif mode == 'bad_authority':
                    invalid['people'][0]['disclosure_authority'] = 'house_clerk'
                elif mode == 'bad_structure':
                    invalid['transactions'][0]['unexpected'] = 'invalid'
                else:
                    invalid['transactions'][0]['transaction_date'] = 'invalid-date'
                (root / 'senate_efd.json').write_text(json.dumps(invalid))
                self.assertEqual(invoke([*args, '--defer-stale-sources']), 2)
            # The opt-in cannot overwrite its input candidate with a status.
            alias_args = list(args)
            alias_args[-1] = str(root / 'house_clerk.json')
            self.assertEqual(invoke([*alias_args, '--defer-stale-sources']), 2)
            (root / 'senate_efd.json').write_text(json.dumps(values['senate_efd']))
            for mode in ('duplicate_health', 'invalid_meta'):
                invalid_base = json.loads((ROOT / 'backend/data/empty-disclosure.json').read_text(encoding='utf-8'))
                if mode == 'duplicate_health':
                    invalid_base['source_health'] = [values['senate_efd']['source_health'][0]] * 2
                else:
                    invalid_base['meta']['data_cutoff_at'] = 'invalid'
                base_path = root / 'invalid-base.json'
                base_path.write_text(json.dumps(invalid_base))
                bad_args = list(args)
                bad_args[2] = str(base_path)
                self.assertEqual(invoke([*bad_args, '--defer-stale-sources']), 2)


class WorkflowAvailabilityTests(recovery.SourceWorkflowRecoveryTests):
    # Inherited tests execute the source-persistence and deferred-assembly Bash.
    def test_actual_collection_cli_records_refusal_without_touching_old_state_or_discovery(self):
        prior = {'status': 'catalog_ready_for_review',
                 'catalog': {'retrieved_at': '2026-10-23T07:42:50Z', 'sha256': 'a' * 64},
                 'reports': {'evidence_count': 129}, 'roster': {'sha256': 'b' * 64}}
        self.write('status/senate_efd.json', prior)
        self.git('-C', str(self.review), 'add', '.')
        self.git('-C', str(self.review), 'commit', '-m', 'prior source')
        self.git('-C', str(self.review), 'push', 'origin', 'HEAD:refs/heads/state')
        shutil.copytree(ROOT / 'backend/src', self.root / 'backend/src',
                        ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
        # Inject one HTTP refusal below the actual client, without live traffic.
        (self.root / 'backend/src/sitecustomize.py').write_text('''
import urllib.request, urllib.error
from io import BytesIO
class RefusingOpener:
    def open(self, request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden',
                                     {'Server':'AkamaiGHost'}, BytesIO(b'private body'))
urllib.request.build_opener = lambda *args: RefusingOpener()
''', encoding='utf-8')
        local = self.root / '.local'
        local.mkdir()
        discovery = local / 'senate-efd-discovery.json'
        discovery.write_bytes(b'prior discovery retained')
        self.env.update(SENATE_EFD_COLLECTION_ENABLED='true',
                        SENATE_EFD_TERMS_ACKNOWLEDGED='true', SENATE_EFD_START_DATE='2026-01-01',
                        GITHUB_OUTPUT=str(self.root / 'github-output'),
                        GITHUB_STEP_SUMMARY=str(self.root / 'github-summary'),
                        EVIDENCE_ROOT=(self.root / 'evidence').as_posix())
        result = self.run_shell(step_script('senate.yml',
                                'Verify authorization gate and collect PTR catalog'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('source_status=blocked', (self.root / 'github-output').read_text())
        self.assertEqual(discovery.read_bytes(), b'prior discovery retained')
        self.assertFalse((self.root / 'evidence').exists())
        self.env.update(SOURCE_STATUS='blocked', COLLECT_OUTCOME='success')
        result = self.run_shell(step_script('senate.yml',
                         'Record failed catalog collection without advancing source data'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        saved = json.loads((self.review / 'status/senate_efd.json').read_bytes())
        self.assertEqual(saved['status'], 'blocked')
        self.assertEqual(saved['last_collection_attempt']['failed_step'], 'collect_catalog')
        self.assertEqual(saved['last_collection_attempt']['http_status'], 403)
        for key in ('catalog', 'reports', 'roster'):
            self.assertEqual(saved[key], prior[key])
        self.assertIn('no new Senate data was produced', (self.root / 'github-summary').read_text())
