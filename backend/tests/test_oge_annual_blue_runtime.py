"""Transport contracts for the existing blue reader; no model inference."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_oge_annual_saved_scan_runtime as saved_fixture
from unison_snapshot import oge_annual_runtime as runtime
from unison_snapshot import oge_handwritten_date as blue


class AnnualBlueRuntimeTests(unittest.TestCase):
    def setUp(self):
        # Reuse a generated native source and the existing observation writers.
        self.fixture = saved_fixture.SavedScanRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.seed(native_cover=True)
        self.review, self.evidence = self.fixture.review, self.fixture.evidence
        self.model_directory = 'optional/blue-date-v1'
        model = self.evidence / self.model_directory
        model.mkdir(parents=True)
        # Small fake pinned files test packaging only, never recognition.
        self.model_bytes, self.config_bytes = b'packaging-model', b'packaging-config'
        (model / 'model.onnx').write_bytes(self.model_bytes)
        (model / 'config.json').write_bytes(self.config_bytes)
        pins = patch.multiple(blue,
            MODEL_SHA256=hashlib.sha256(self.model_bytes).hexdigest(),
            CONFIG_SHA256=hashlib.sha256(self.config_bytes).hexdigest())
        pins.start(); self.addCleanup(pins.stop)

    def prepare(self, **kwargs):
        return runtime.prepare_controlled_annual_input(self.review, self.evidence,
            code_commit='a'*40, started_at='2026-10-27T00:00:00Z',
            include_saved_scans=True, **kwargs)

    def selected(self, **kwargs):
        return self.prepare(blue_date_document_ids=[saved_fixture.DOC],
            blue_date_model_directory=self.model_directory, **kwargs)

    def verify(self, value):
        return runtime.verify_controlled_annual_input(value,
            review_root=self.review, evidence_root=self.evidence)

    def test_selected_dependencies_are_portable_pinned_and_never_executed(self):
        originals = {path: path.read_bytes() for root in (self.review, self.evidence)
                     for path in root.rglob('*') if path.is_file()}
        with (patch.object(blue, 'replay_annual_filer_date', side_effect=AssertionError('inference')),
              patch.object(blue, '_read_ctc', side_effect=AssertionError('inference'))):
            value = self.selected()
            self.assertEqual(self.verify(value), value['controlled_annual_runtime'])
        self.assertEqual(value['handwritten_date_inputs_by_document'], {saved_fixture.DOC: {
            'archived_pdf_path': self.fixture.pdf_relative,
            'model_directory': self.model_directory}})
        receipt = value['controlled_annual_runtime']
        self.assertFalse(receipt['recognizer_started'])
        self.assertEqual(receipt['blue_date_document_ids'], [saved_fixture.DOC])
        model_files = [entry for entry in receipt['files'] if entry['path'].startswith(self.model_directory)]
        self.assertEqual(len(model_files), 2)
        self.assertEqual(receipt['total_bytes'], sum(entry['bytes'] for entry in receipt['files']))
        self.assertEqual(receipt['total_bytes'] - self.prepare()['controlled_annual_runtime']['total_bytes'],
                         len(self.model_bytes) + len(self.config_bytes))
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)
        moved = self.fixture.root / 'relocated'
        moved.mkdir()
        for root in (self.review, self.evidence):
            shutil.move(str(root), str(moved / root.name))
        self.review, self.evidence = moved / 'review', moved / 'evidence'
        self.assertEqual(self.selected(), value)
        self.verify(value)

    def test_default_remains_exactly_the_existing_package_and_needs_no_model(self):
        value = self.prepare()
        # New default arguments must not change even the serialized key set.
        explicit = self.prepare(blue_date_document_ids=None, blue_date_model_directory=None,
                                verify_transaction_blank_slots=False)
        self.assertEqual(json.dumps(value), json.dumps(explicit))
        self.assertNotIn('handwritten_date_inputs_by_document', value)
        for key in ('blue_date_document_ids', 'blue_date_model_directory', 'verify_transaction_blank_slots'):
            self.assertNotIn(key, value['controlled_annual_runtime'])
        self.assertNotIn('verify_transaction_blank_slots', value['saved_scan_observations_by_document'][saved_fixture.DOC])
        shutil.rmtree(self.evidence / self.model_directory)
        self.assertEqual(self.prepare(), value)
        self.verify(value)

    def test_selection_is_paired_bounded_and_only_for_selected_scans(self):
        cases = [dict(blue_date_document_ids=[saved_fixture.DOC]),
            dict(blue_date_model_directory=self.model_directory),
            dict(blue_date_document_ids=[], blue_date_model_directory=self.model_directory),
            dict(blue_date_document_ids=[saved_fixture.DOC]*2, blue_date_model_directory=self.model_directory),
            dict(blue_date_document_ids=['z'*32], blue_date_model_directory=self.model_directory),
            dict(blue_date_document_ids=['a'*32], blue_date_model_directory=self.model_directory),
            dict(blue_date_document_ids=[f'{index:032x}' for index in range(runtime.MAX_DOCUMENTS+1)],
                 blue_date_model_directory=self.model_directory)]
        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.prepare(**arguments)
        with self.assertRaises(ValueError):
            runtime.prepare_controlled_annual_input(self.review, self.evidence,
                code_commit='a'*40, started_at='2026-10-27T00:00:00Z',
                blue_date_document_ids=[saved_fixture.DOC], blue_date_model_directory=self.model_directory)

    def test_model_directory_must_be_canonical_inside_evidence(self):
        for directory in ('', '.', '..', '../blue', '/blue', 'C:/blue', r'optional\blue-date-v1',
                          'optional//blue-date-v1', 'optional/./blue-date-v1',
                          'optional/blue-date-v1/', 'optional/blue-date-v1.', 'optional/blue-date-v1 '):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                self.prepare(blue_date_document_ids=[saved_fixture.DOC], blue_date_model_directory=directory)
        alias = self.evidence / 'alias'
        try:
            alias.symlink_to(self.evidence / self.model_directory, target_is_directory=True)
        except OSError:
            pass  # Windows symlink privilege is optional; lexical controls still run.
        else:
            with self.assertRaisesRegex(ValueError, 'alias'):
                self.prepare(blue_date_document_ids=[saved_fixture.DOC], blue_date_model_directory='alias')

    def test_wrong_or_missing_pinned_files_and_total_budget_reject(self):
        model = self.evidence / self.model_directory
        for name, content in (('model.onnx', self.model_bytes), ('config.json', self.config_bytes)):
            path = model / name
            path.write_bytes(b'wrong bytes')
            with self.subTest(file=name), self.assertRaisesRegex(ValueError, 'hash differs'):
                self.selected()
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                self.selected()
            path.write_bytes(content)
        baseline = self.prepare()['controlled_annual_runtime']['total_bytes']
        with patch.object(runtime, 'MAX_TOTAL_BYTES', baseline + len(self.model_bytes) + len(self.config_bytes) - 1):
            with self.assertRaisesRegex(ValueError, 'byte budget'):
                self.selected()

    def test_blank_flag_uses_existing_source_preflight_and_is_not_a_date(self):
        from unison_snapshot.oge_annual_scan_sections import verify_part7_empty_slots
        with patch('unison_snapshot.oge_annual_scan_sections.verify_part7_empty_slots',
                   wraps=verify_part7_empty_slots) as replay:
            default = self.prepare()
            replay.assert_not_called()
            value = self.prepare(verify_transaction_blank_slots=True)
            replay.assert_called_once()
        self.assertTrue(value['saved_scan_observations_by_document'][saved_fixture.DOC]['verify_transaction_blank_slots'])
        self.assertTrue(value['controlled_annual_runtime']['verify_transaction_blank_slots'])
        self.assertNotIn('handwritten_date_inputs_by_document', value)
        self.assertEqual(default['controlled_annual_runtime']['files'], value['controlled_annual_runtime']['files'])
        self.verify(value)
        for flag in (1, 'true', None):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.prepare(verify_transaction_blank_slots=flag)
        with self.assertRaises(ValueError):
            runtime.prepare_controlled_annual_input(self.review, self.evidence,
                code_commit='a'*40, started_at='2026-10-27T00:00:00Z', verify_transaction_blank_slots=True)

    def test_tampered_maps_receipt_and_self_signed_dependencies_do_not_verify(self):
        value = self.selected(verify_transaction_blank_slots=True)
        mutations = [
            lambda x: x['handwritten_date_inputs_by_document'][saved_fixture.DOC].update(model_directory='other'),
            lambda x: x['handwritten_date_inputs_by_document'][saved_fixture.DOC].update(date='2023-05-08'),
            lambda x: x['handwritten_date_inputs_by_document'][saved_fixture.DOC].update(archived_pdf_path='other.pdf'),
            lambda x: x['saved_scan_observations_by_document'][saved_fixture.DOC].pop('verify_transaction_blank_slots'),
            lambda x: x['controlled_annual_runtime'].update(verify_transaction_blank_slots=False),
            lambda x: x['controlled_annual_runtime'].pop('blue_date_document_ids'),
            lambda x: x['controlled_annual_runtime'].update(blue_date_document_ids=['a'*32]),
        ]
        for mutate in mutations:
            altered = deepcopy(value); mutate(altered)
            with self.subTest(value=altered['controlled_annual_runtime']), self.assertRaises(ValueError):
                self.verify(altered)
        path = self.evidence / self.model_directory / 'model.onnx'
        path.write_bytes(b'self signed replacement')
        altered = deepcopy(value)
        for entry in altered['controlled_annual_runtime']['files']:
            if entry['path'] == path.relative_to(self.evidence).as_posix():
                altered['controlled_annual_runtime']['total_bytes'] += path.stat().st_size - entry['bytes']
                entry.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'hash differs'):
            self.verify(altered)


if __name__ == '__main__':
    unittest.main()
