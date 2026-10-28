"""Generic annual observation writers retain their normal runtime consumer."""
from pathlib import Path
import json
import unittest
import test_oge_annual_saved_scan_runtime as scan_fixtures
from test_oge_annual_candidate import base
from unison_snapshot.oge_annual_runtime import controlled_scan_output_path


class AnnualObservationEntryTests(unittest.TestCase):
    def test_normal_writer_prepares_and_overlays_without_moving_legacy_observations(self):
        case=scan_fixtures.SavedScanRuntimeTests();case.setUp();self.addCleanup(case.doCleanups)
        case.seed()  # Generated PDF and a fixed prior cover observation; no OCR.
        standard=controlled_scan_output_path(case.review,'section_observation',scan_fixtures.DOC,case.sha)
        legacy=case.review/f'oge/annual/scan_section_observations/{scan_fixtures.DOC}/{case.sha}/sections-v1.json'
        arguments=['--pdf',case.pdf,'--source-sha256',case.sha,'--document-id',scan_fixtures.DOC]
        result=case.cli('observe-oge-annual-scan-sections',[*arguments,'--output',legacy])
        self.assertEqual(result.returncode,0,result.stderr)
        legacy_raw=legacy.read_bytes();self.assertEqual(legacy_raw,standard.read_bytes())
        standard.unlink();case.input.write_bytes(b'prior runtime input')
        result=case.prepare()
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'lacks its required cover or section inventory',result.stderr)
        self.assertEqual(case.input.read_bytes(),b'prior runtime input')
        result=case.cli('observe-oge-annual-scan-sections',[*arguments,'--review-root',case.review])
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(standard.read_bytes(),legacy_raw)
        result=case.prepare();self.assertEqual(result.returncode,0,result.stderr)
        package=json.loads(case.input.read_bytes())
        paths=[item['path']for item in package['controlled_annual_runtime']['files']]
        self.assertIn(standard.relative_to(case.review).as_posix(),paths)
        self.assertNotIn(legacy.relative_to(case.review).as_posix(),paths)
        case.write(case.candidate,base());result=case.overlay()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(case.candidate.read_bytes())['transactions'],base()['transactions'])
        self.assertEqual(legacy.read_bytes(),legacy_raw)
        before=standard.read_bytes()
        wrong=['--pdf',case.pdf,'--source-sha256','0'*64,'--document-id',scan_fixtures.DOC,
               '--output',standard]
        result=case.cli('observe-oge-annual-scan-sections',wrong)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(standard.read_bytes(),before)
        self.assertEqual(legacy.read_bytes(),legacy_raw)
