"""Consume explicit cover replay without changing the existing OCR reader."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import test_oge_annual_saved_scan_runtime as saved_fixture
from unison_snapshot import oge_annual_runtime as runtime
from unison_snapshot.oge import OgeCatalogError


FULL_COVER = ("OGE Form 278e\nReport Type: Annual\nYear (Annual Report only): 2025\n"
              "Last Name First Name\nExample Ada\nFiler's Certification\nDate: 06/01/2026\n"
              "Agency Ethics Official's Opinion\nDate: 06/02/2026")
WEAK_COVER = FULL_COVER.replace('Report Type: Annual', 'Annual GOVERNMENT ETHICS').replace(
    'Year (Annual Report only): 2025', '2025 *')
OBSERVER = 'unison_snapshot.oge_annual_scan_cover.observe_scan_cover'


class AnnualCoverReplayRuntimeTests(unittest.TestCase):
    def case(self, text=None, page_size=None):
        case = saved_fixture.SavedScanRuntimeTests()
        case.setUp(); self.addCleanup(case.doCleanups)
        case.metadata['report_year'] = 2026  # Catalog context, never the source report year.
        case.write(case.pdf.with_suffix('.json'), case.metadata)
        if text is not None or page_size is not None:
            import pymupdf
            document = pymupdf.open(case.pdf)
            document.delete_page(0)
            page = document.new_page(pno=0, **(
                dict(width=page_size[0], height=page_size[1]) if page_size is not None else {}))
            page.insert_text((30, 40), FULL_COVER if text is None else text)
            raw = document.tobytes(); document.close()
            case.sha = hashlib.sha256(raw).hexdigest()
            case.pdf_relative = f'oge/annual/reports/{saved_fixture.DOC}/{case.sha}.pdf'
            case.pdf = case.evidence / case.pdf_relative
            case.pdf.parent.mkdir(parents=True, exist_ok=True)
            case.pdf.write_bytes(raw)
            case.metadata.update(sha256=case.sha, archive_path=case.pdf_relative)
            case.write(case.pdf.with_suffix('.json'), case.metadata)
        cover, _ = case.seed(native_cover=True)
        cover.update(cover_page_number=1, catalog_filer='Example, Ada', catalog_year_label=2025,
            ocr_engine='saved-engine', selected_cover_lines=[{'text': 'Report Type: Annual',
                'top': 40, 'x0': 30, 'x1': 180, 'ocr_confidence': 99}],
            receipt_date_verified=False, production_qualification_authorized=False)
        self.write_cover(case, cover)
        return case, cover

    @staticmethod
    def write_cover(case, cover):
        path = runtime.controlled_scan_output_path(case.review, 'cover_observation',
                                                  saved_fixture.DOC, case.sha)
        case.write(path, cover)

    @staticmethod
    def prepare(case, **kwargs):
        return runtime.prepare_controlled_annual_input(case.review, case.evidence,
            code_commit='a'*40, started_at='2026-10-28T00:00:00Z',
            include_saved_scans=True, **kwargs)

    @staticmethod
    def verify(case, value):
        return runtime.verify_controlled_annual_input(value,
            review_root=case.review, evidence_root=case.evidence)

    def test_default_never_invokes_observer_and_explicit_flag_requires_scans(self):
        case, _ = self.case()
        with patch(OBSERVER, side_effect=AssertionError('unexpected OCR')):
            baseline = self.prepare(case)
            explicit_false = self.prepare(case, replay_cover_ocr=False)
            self.assertEqual(json.dumps(baseline), json.dumps(explicit_false))
            self.verify(case, baseline)
        self.assertFalse(baseline['controlled_annual_runtime']['recognizer_started'])
        self.assertNotIn('replay_cover_ocr', baseline['controlled_annual_runtime'])
        for flag in (1, 'true', None):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.prepare(case, replay_cover_ocr=flag)
        with self.assertRaises(ValueError):
            runtime.prepare_controlled_annual_input(case.review, case.evidence,
                code_commit='a'*40, started_at='2026-10-28T00:00:00Z', replay_cover_ocr=True)

    def test_explicit_replays_source_once_and_preserves_raw_observation(self):
        case, cover = self.case()
        actual = deepcopy(cover)
        actual['ocr_engine'] = 'current-engine'
        actual['selected_cover_lines'][0]['ocr_confidence'] = 74
        original = {path: path.read_bytes() for root in (case.review, case.evidence)
                    for path in root.rglob('*') if path.is_file()}
        with patch(OBSERVER, return_value=actual) as observer:
            value = self.prepare(case, replay_cover_ocr=True)
            observer.assert_called_once()
        args, kwargs = observer.call_args
        self.assertEqual(args, (case.pdf.resolve(),))
        self.assertEqual(kwargs['source_sha256'], case.sha)
        self.assertEqual(kwargs['document_id'], saved_fixture.DOC)
        self.assertEqual(kwargs['catalog_filer'], case.metadata['filer_name'])
        self.assertEqual(kwargs['catalog_year'], case.metadata['report_year'])
        self.assertEqual(value['saved_scan_observations_by_document'][saved_fixture.DOC]['cover_observation'], cover)
        self.assertTrue(value['controlled_annual_runtime']['recognizer_started'])
        self.assertTrue(value['controlled_annual_runtime']['replay_cover_ocr'])
        proof, = value['controlled_annual_runtime']['cover_source_replays']
        self.assertEqual(proof['method'], 'source_ocr_cover_fields/v1')
        self.assertEqual(proof['document_id'], saved_fixture.DOC)
        self.assertEqual(proof['source_sha256'], case.sha)
        self.assertEqual(proof['ocr_engine'], actual['ocr_engine'])
        self.assertEqual(proof['projection'], {key: cover[key] for key in (
            'schema_version', 'document_id', 'source_sha256', 'page_count', 'cover_page_number',
            'native_text_sha256', 'form_278e_text_observed', 'annual_type_text_observed',
            'annual_year_text_candidates')})
        self.assertRegex(proof['observation_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(value['extractions'], [])
        self.assertEqual(value['controlled_annual_runtime']['documents'][0]['qualification_status'],
                         'deferred_to_existing_consumer')
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)

    def test_authoritative_projection_requires_actual_source_and_strict_types(self):
        case, cover = self.case()
        changes = [('schema_version', 'other'), ('document_id', 'a'*32),
            ('source_sha256', '0'*64), ('page_count', 3), ('cover_page_number', 2),
            ('cover_page_number', True), ('native_text_sha256', '0'*64),
            ('form_278e_text_observed', False), ('form_278e_text_observed', 1),
            ('annual_type_text_observed', False), ('annual_type_text_observed', 1),
            ('annual_year_text_candidates', []), ('annual_year_text_candidates', [2024]),
            ('annual_year_text_candidates', [2025.0]), ('annual_year_text_candidates', [True]),
            ('annual_year_text_candidates', [2025, 2025])]
        for key, replacement in changes:
            actual = dict(deepcopy(cover), **{key: replacement})
            with (self.subTest(key=key, value=replacement), patch(OBSERVER, return_value=actual),
                  self.assertRaises((ValueError, OgeCatalogError))):
                self.prepare(case, replay_cover_ocr=True)
        # The saved side must satisfy the same strict source projection.
        forged = dict(cover, cover_page_number=True)
        self.write_cover(case, forged)
        with patch(OBSERVER, return_value=forged), self.assertRaises((ValueError, OgeCatalogError)):
            self.prepare(case, replay_cover_ocr=True)

    def test_catalog_engine_and_extra_fields_cannot_supply_report_year_or_qualification(self):
        case, cover = self.case()
        case.metadata['report_year'] = 2026
        case.write(case.pdf.with_suffix('.json'), case.metadata)
        cover.update(catalog_year_label=2027, catalog_filer='non-authoritative catalog value',
            filing_date='2099-01-01', eligibility='eligible', unknown_extra={'year': 1900})
        self.write_cover(case, cover)
        actual = deepcopy(cover)
        actual.update(catalog_year_label=2026, catalog_filer='Example, Ada', ocr_engine='different')
        actual['selected_cover_lines'][0]['ocr_confidence'] = 0
        with patch(OBSERVER, return_value=actual):
            value = self.prepare(case, replay_cover_ocr=True)
        self.assertEqual(value['saved_scan_observations_by_document'][saved_fixture.DOC]['cover_observation'], cover)
        path = runtime.controlled_scan_output_path(case.review, 'cover_observation', saved_fixture.DOC, case.sha)
        dependency, = [entry for entry in value['controlled_annual_runtime']['files']
                       if entry['root'] == 'review' and entry['path'] == path.relative_to(case.review).as_posix()]
        self.assertEqual(dependency['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertNotIn('handwritten_date_inputs_by_document', value)
        self.assertEqual(value['extractions'], [])
        # Neither catalog year nor certification date may fill an absent OCR year.
        missing = dict(actual, annual_year_text_candidates=[])
        self.write_cover(case, dict(cover, annual_year_text_candidates=[]))
        with patch(OBSERVER, return_value=missing), self.assertRaises((ValueError, OgeCatalogError)):
            self.prepare(case, replay_cover_ocr=True)

    def test_weak_native_cover_requires_explicit_source_replay(self):
        for text in (WEAK_COVER, FULL_COVER.replace('Report Type: Annual\n', '').replace(
                     'Year (Annual Report only): 2025\n', '')):
            case, cover = self.case(text)
            with patch(OBSERVER, side_effect=AssertionError('default OCR')):
                with self.assertRaises(ValueError):
                    self.prepare(case)
            with patch(OBSERVER, return_value=cover):
                value = self.prepare(case, replay_cover_ocr=True)
            self.assertTrue(value['controlled_annual_runtime']['recognizer_started'])

    def test_native_explicit_contradictions_name_and_whole_source_remain_rejected(self):
        for text in (FULL_COVER.replace('Report Type: Annual', 'Report Type: Termination'),
                     FULL_COVER.replace('only): 2025', 'only): 2024'),
                     FULL_COVER.replace('Example Ada', 'Other Person')):
            case, cover = self.case(text)
            with patch(OBSERVER, return_value=cover), self.assertRaises(ValueError):
                self.prepare(case, replay_cover_ocr=True)
        case, cover = self.case(WEAK_COVER)
        for role in ('native_cover_observation', 'section_observation'):
            path = runtime.controlled_scan_output_path(case.review, role, saved_fixture.DOC, case.sha)
            original = path.read_bytes()
            altered = json.loads(original)
            altered['unreplayed_extra'] = True
            case.write(path, altered)
            with self.subTest(role=role), patch(OBSERVER, return_value=cover), self.assertRaises(ValueError):
                self.prepare(case, replay_cover_ocr=True)
            path.write_bytes(original)

    def test_receipt_rebuild_replays_and_rejects_self_signed_source_claims(self):
        case, cover = self.case(WEAK_COVER)
        with patch(OBSERVER, return_value=cover):
            value = self.prepare(case, replay_cover_ocr=True)
        with patch(OBSERVER, return_value=cover) as observer:
            self.verify(case, value)
            observer.assert_called_once()
        for key, replacement in (('recognizer_started', False), ('replay_cover_ocr', False)):
            altered = deepcopy(value)
            altered['controlled_annual_runtime'][key] = replacement
            with self.subTest(key=key), patch(OBSERVER, return_value=cover), self.assertRaises(ValueError):
                self.verify(case, altered)
        for key, replacement in (('observation_sha256', '0'*64), ('ocr_engine', 'self-signed')):
            altered = deepcopy(value)
            altered['controlled_annual_runtime']['cover_source_replays'][0][key] = replacement
            with self.subTest(proof_key=key), patch(OBSERVER, return_value=cover), self.assertRaises(ValueError):
                self.verify(case, altered)
        altered = deepcopy(value)
        forged = altered['saved_scan_observations_by_document'][saved_fixture.DOC]['cover_observation']
        forged['annual_year_text_candidates'] = [2024]
        self.write_cover(case, forged)
        path = runtime.controlled_scan_output_path(case.review, 'cover_observation', saved_fixture.DOC, case.sha)
        for entry in altered['controlled_annual_runtime']['files']:
            if entry['root'] == 'review' and entry['path'] == path.relative_to(case.review).as_posix():
                altered['controlled_annual_runtime']['total_bytes'] += path.stat().st_size-entry['bytes']
                entry.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        with patch(OBSERVER, return_value=cover), self.assertRaises(ValueError):
            self.verify(case, altered)

    def test_omitting_saved_native_role_cannot_bypass_actual_source_identity(self):
        for text, valid in ((WEAK_COVER, True), (WEAK_COVER.replace('Example Ada', 'Other Person'), False)):
            case, cover = self.case(text)
            path = runtime.controlled_scan_output_path(case.review, 'native_cover_observation',
                                                      saved_fixture.DOC, case.sha)
            path.unlink()
            with patch(OBSERVER, return_value=cover):
                if valid:
                    value = self.prepare(case, replay_cover_ocr=True)
                    self.assertNotIn('native_cover_observation',
                        value['saved_scan_observations_by_document'][saved_fixture.DOC])
                else:
                    with self.assertRaises(ValueError):
                        self.prepare(case, replay_cover_ocr=True)

    def test_oversized_source_cover_rejects_before_observer_without_rendering(self):
        case, cover = self.case(page_size=(8000, 8000))
        with patch(OBSERVER, side_effect=AssertionError('must reject before OCR')) as observer:
            baseline = self.prepare(case)
            self.assertFalse(baseline['controlled_annual_runtime']['recognizer_started'])
            self.assertEqual(baseline['saved_scan_observations_by_document'][saved_fixture.DOC]['cover_observation'], cover)
            with self.assertRaisesRegex(ValueError, 'pixel budget'):
                self.prepare(case, replay_cover_ocr=True)
            observer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
