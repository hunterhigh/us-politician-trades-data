import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class OgeManualCoverDateTests(unittest.TestCase):
    def test_date_correction_is_not_reused_for_a_different_parser(self):
        from unison_snapshot.oge_manual_dates import apply_manual_cover_dates
        report = {'document_id': 'a'*32, 'source_sha256': 'b'*64,
                  'parser_version': 'oge-278t-native-roles/v7', 'filed_at': None}
        with tempfile.TemporaryDirectory() as folder:
            self.assertIs(apply_manual_cover_dates([report], Path(folder))[0], report)



if __name__ == "__main__":
    unittest.main()
