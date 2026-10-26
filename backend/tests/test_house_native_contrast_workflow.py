"""Execute the normal House selector before either cache success/failure skip."""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

import test_house_native_contrast as core
from unison_snapshot.house_ptr import PARSER_RETRY_VERSION, LEGACY_PARSER_VERSION


class HouseContrastWorkflowTests(unittest.TestCase):
    def test_actual_source_selection_precedes_old_success_and_failure(self):
        self.assert_source_selection(core.HouseNativeContrastTests(), 'white')

    def test_opacity_source_selection_requeues_old_cache_before_success_skip(self):
        from test_house_native_opacity import HouseNativeOpacityTests
        self.assert_source_selection(HouseNativeOpacityTests(), 'ca0')

    def assert_source_selection(self, helper, kind):
        self.addCleanup(helper.doCleanups)
        root,legacy,current=helper.source(kind)
        metadata=root/Path(legacy['source']['archive_path']).with_suffix('.json')
        # The opacity fixture also constructed its control source. Keep this
        # selector contract scoped to the one archived version under test.
        for setup_metadata in metadata.parent.glob('*.json'):
            if setup_metadata != metadata:
                setup_metadata.unlink()
        review=root/'review';review.mkdir()
        relative=Path('2026/20000001')/(legacy['source_sha256']+'.json')
        extraction=review/'house_clerk/extractions'/relative
        extraction.parent.mkdir(parents=True)
        extraction.write_text(json.dumps(legacy),encoding='utf-8')
        repo=Path(__file__).resolve().parents[2]
        workflow=(repo/'.github/workflows/house-review.yml').read_text(encoding='utf-8')
        segment=workflow.split('- name: Select unprocessed evidence',1)[1].split('- name:',1)[0]
        script=textwrap.dedent(segment.split("python - <<'PY'",1)[1].split('\n          PY',1)[0])
        environment={**os.environ,'PYTHONPATH':str(repo/'backend/src'),'EVIDENCE_ROOT':str(root),
            'REVIEW_ROOT':str(review),'PARSER_COMMIT':'a'*40,'BATCH_SIZE':'25',
            'LEGACY_PARSER_VERSION':LEGACY_PARSER_VERSION,'PARSER_RETRY_VERSION':PARSER_RETRY_VERSION}

        def selected():
            before={path:path.read_bytes() for path in review.rglob('*.json')}
            done=subprocess.run([sys.executable,'-c',script],cwd=root,env=environment,
                capture_output=True,text=True,timeout=30)
            self.assertEqual(done.returncode,0,done.stderr)
            self.assertEqual(before,{path:path.read_bytes() for path in before})
            return (root/'.local/parse-batch.txt').read_text(encoding='utf-8').splitlines()

        self.assertEqual(selected(),[str(metadata)])
        extraction.write_text(json.dumps(current),encoding='utf-8')
        self.assertEqual(selected(),[])
        extraction.unlink()
        failure=review/'house_clerk/failures'/relative;failure.parent.mkdir(parents=True)
        failure.write_text(json.dumps({'parser_version':'house-parser-suite-2026-12-image-paint-order'}),encoding='utf-8')
        self.assertEqual(selected(),[str(metadata)])
        failure.write_text(json.dumps({'parser_version':PARSER_RETRY_VERSION}),encoding='utf-8')
        self.assertEqual(selected(),[])
        # An unreadable source is queued for normal failure accounting; it
        # cannot make an old successful extraction an absence proof.
        failure.unlink();extraction.write_text(json.dumps(legacy),encoding='utf-8')
        pdf=root/legacy['source']['archive_path'];pdf.write_bytes(pdf.read_bytes()+b'\n% changed source')
        self.assertEqual(selected(),[str(metadata)])
