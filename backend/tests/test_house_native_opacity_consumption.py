"""House binary-alpha sources through ordinary CLI, status and reader."""
import unittest
from unittest.mock import patch

import test_house_native_opacity as opacity_tests
import test_house_native_contrast_consumption as shared
from unison_snapshot.disclosure_status_house import build_house_status


class HouseNativeOpacityConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.driver = shared.HouseNativeContrastConsumptionTests()
        self.driver.setUp()
        self.addCleanup(self.driver.doCleanups)
        self.opacity = opacity_tests.HouseNativeOpacityTests()
        self.addCleanup(self.opacity.doCleanups)
        self.driver.helper = self.opacity

    def test_normal_cli_preserves_independent_rows_and_recovers_visible_state(self):
        for kind,count in [('ca0',1),('CA0',2),('restore_same_q',2),
                ('restore_inside_BT',2),('restore_global',1),('transparent_cover',2)]:
            with self.subTest(kind=kind):
                data = self.driver.prepare(kind)
                self.assertEqual(data['q']['parser_version'],'house-ptr-native-opacity/v1')
                candidate = self.driver.candidate(data)
                self.assertEqual(len(candidate['transactions']),count)
                scopes = build_house_status(data['evidence'],data['review'],candidate['transactions'],[])
                record = scopes[0]['reports'][0]
                self.assertEqual(record['qualification_state'],'partial')
                self.assertEqual(record['qualified_transaction_count'],count)
                self.assertFalse(record['source_observation_coverage']['coverage_complete'])
                self.assertEqual(len(record['publication_transaction_ids']),count)
                self.driver.reader(candidate,scopes)
                before = (data['root']/'candidate.json').read_bytes()
                self.driver.candidate(data,prior_path=data['root']/'candidate.json')
                self.assertEqual(before,(data['root']/'candidate.json').read_bytes())

    def test_old_full_formal_fact_conservation_reuses_actual_source_contract(self):
        source = self.opacity.source
        with patch.object(self.opacity,'source',side_effect=lambda kind:source('ca0')):
            self.driver.test_unknown_current_source_preserves_complete_old_formal_and_ids()

    def test_source_roots_forged_saved_members_and_changed_pdf_fail_before_output(self):
        source = self.opacity.source
        with patch.object(self.opacity,'source',side_effect=lambda kind:source('ca0')):
            self.driver.test_missing_roots_resigned_source_and_changed_pdf_do_not_overwrite_output()


if __name__ == '__main__':
    unittest.main()
