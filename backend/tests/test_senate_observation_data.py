import json
from pathlib import Path
import shutil
import tempfile
import unittest

from unison_snapshot.senate_observation_data import DEFAULT_ROOT,load_bundle


class SourceObservationBundleTests(unittest.TestCase):
    def test_all_saved_observations_match_the_bundle_and_changed_bytes_fail(self):
        index=load_bundle()
        self.assertTrue(index['file_sha256'])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for path in DEFAULT_ROOT.glob('*.json'):shutil.copyfile(path,root/path.name)
            self.assertEqual(load_bundle(root),index)
            path=root/index['ledgers'][0];path.write_bytes(path.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError,'hash changed'):load_bundle(root)

    def test_data_index_cannot_select_an_outside_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for path in DEFAULT_ROOT.glob('*.json'):shutil.copyfile(path,root/path.name)
            index=json.loads((root/'index.json').read_bytes())
            old=index['ledgers'][0];index['ledgers'][0]='../outside.json'
            index['file_sha256']['../outside.json']=index['file_sha256'].pop(old)
            (root/'index.json').write_text(json.dumps(index),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'canonical'):load_bundle(root)
