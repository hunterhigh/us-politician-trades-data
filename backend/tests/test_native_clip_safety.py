"""Original-source authentication, conservation and nested clipped targets."""
import json
import unittest
from unittest.mock import patch

import test_native_partition_consumption as normal
import test_native_bichrome_safety as shared_safety
import test_native_partition_exhibit as exhibit_helpers
from test_native_clip_consumption import source_pdf
from unison_snapshot.oge import OgeCatalogError
from whitehouse_account_display_fixture import encoded, write_files


class NativeClipSafetyTests(unittest.TestCase):
    def setUp(self):
        self.helper = shared_safety.BichromeSafetyTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)

    def test_actual_current_source_replay_refuses_resigned_cache_and_source_mutations(self):
        with patch.object(shared_safety,'source_pdf',return_value=source_pdf('outside')):
            self.helper.test_saved_proof_resigning_roots_metadata_and_source_fail_before_output()

    def test_original_full_formal_facts_and_ids_survive_new_visibility_policy(self):
        with patch.object(shared_safety,'source_pdf',return_value=source_pdf('outside')):
            self.helper.test_old_complete_formal_objects_and_ids_are_conserved_not_copied()

    def test_real_status_uses_source_counts_and_rejects_forged_qualification(self):
        with patch.object(shared_safety,'source_pdf',return_value=source_pdf('outside')):
            self.helper.test_normal_status_is_source_replayed_partial_and_counter_tampering_fails()

    def test_previous_same_sha_successes_cannot_substitute_new_selected_policy(self):
        from unison_snapshot.oge_reports import load_reusable_extraction
        for whitehouse in (False,True):
            for generic in (False,True):
                with self.subTest(whitehouse=whitehouse,generic=generic):
                    with patch.object(shared_safety,'source_pdf',return_value=source_pdf('outside')):
                        data = self.helper.prepare(whitehouse,generic)
                    report = json.loads(data['cache'].read_bytes())
                    self.assertIn('native-clip',report['parser_version'])
                    saved = data['cache'].read_bytes()
                    versions = (['whitehouse-278t-hybrid-geometry/v2','whitehouse-278t-native-roles/v5',
                        'whitehouse-278t-native-visible-default/v1','whitehouse-278t-native-visible-generic/v1',
                        'whitehouse-278t-native-bichrome-default/v1','whitehouse-278t-native-bichrome-generic/v1',
                        'whitehouse-278t-native-opacity-default/v1','whitehouse-278t-native-opacity-generic/v1']
                        if whitehouse else ['oge-278t-pdf/v2','oge-278t-pdf/v7',
                        'oge-278t-native-visible-default/v1','oge-278t-native-visible-generic/v1',
                        'oge-278t-native-bichrome-default/v1','oge-278t-native-bichrome-generic/v1',
                        'oge-278t-native-opacity-default/v1','oge-278t-native-opacity-generic/v1'])
                    for version in versions:
                        write_files(data['cache'].parent,{version.replace('/','-')+'.json':
                            encoded({**report,'parser_version':version})})
                    before = self.helper.snapshot(data)
                    self.helper.candidate(data,generic)
                    self.assertEqual(before,self.helper.snapshot(data))
                    data['cache'].unlink()
                    with self.assertRaises((ValueError,OSError,OgeCatalogError)):
                        self.helper.status_helper.status(data)
                    if not whitehouse:
                        metadata = data['evidence']/data['metadata']['archive_path'].replace('.pdf','.json')
                        self.assertIsNone(load_reusable_extraction(data['evidence'],metadata,
                            data['review']/'oge/extractions',generic=generic))
                    data['cache'].write_bytes(saved)

    def test_actual_exhibit_apply_verify_and_nested_context_reopen_new_policy(self):
        from unison_snapshot.oge_cross_source_replacements import (
            apply_cross_source_native_replacements,verify_cross_source_native_applications,
            replay_cross_source_context)
        helper = exhibit_helpers.NativePartitionExhibitTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        for generic in (False,True):
            with self.subTest(generic=generic):
                raw = source_pdf('inside')
                with patch.object(normal,'normal_pdf',return_value=raw):
                    root,_,before,inputs,saved = helper.inputs('overlay',generic)
                self.assertTrue(all('native-clip' in row['parser_version'] for row in inputs['extractions']))
                result,proof = apply_cross_source_native_replacements(
                    before,saved,inputs,root/'evidence',root/'review')
                self.assertEqual(len(proof['applications']),1)
                self.assertEqual(proof['context']['wh_inputs']['base'],inputs['base'])
                self.assertEqual(verify_cross_source_native_applications(
                    proof,root/'evidence',root/'review'),(result,proof))
                self.assertEqual(replay_cross_source_context(
                    proof['context'],root/'evidence',root/'review'),(result,proof))


if __name__ == '__main__':
    unittest.main()
