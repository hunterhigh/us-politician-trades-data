"""Normal original PDF -> House CLI -> status -> builder/public reader."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_house_native_contrast as core
from test_house_candidate import BASE
from test_house_ptr import IDENTITY
from unison_snapshot.house_ptr import qualify_automatic
from unison_snapshot.disclosure_status_house import build_house_status

REPO = Path(__file__).resolve().parents[2]


class HouseNativeContrastConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.helper = core.HouseNativeContrastTests()
        self.addCleanup(self.helper.doCleanups)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')

    def cli(self, *args, expected=0):
        env = {**os.environ, 'PYTHONPATH':os.pathsep.join(str(REPO/p) for p in ('backend/src','backend','backend/tests')),
               'PYTHONUTF8':'1', 'GIT_NO_LAZY_FETCH':'1'}
        done = subprocess.run([sys.executable, '-m', 'unison_snapshot', *map(str,args)],
            cwd=REPO, env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
        if expected == 0:
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        else:
            self.assertNotEqual(done.returncode, 0, done.stdout)
        return done

    def prepare(self, kind='white', *, prior=False):
        root, raw, _ = self.helper.source(kind)
        evidence, review = root/'e', root/'r'
        sha = raw['source_sha256']; rel = Path('2026/20000001')/(sha+'.json')
        pdf = evidence/'house_clerk/documents'/rel.with_suffix('.pdf')
        pdf.parent.mkdir(parents=True); pdf.write_bytes((root/raw['source']['archive_path']).read_bytes())
        meta = json.loads((root/raw['source']['archive_path']).with_suffix('.json').read_text(encoding='utf-8'))
        meta.update(archive_path=pdf.relative_to(evidence).as_posix(), retrieved_at='2026-09-18T00:00:00Z')
        self.write(pdf.with_suffix('.json'), meta)
        extraction = review/'house_clerk/extractions'/rel
        qualification = review/'house_clerk/qualifications'/rel
        self.write(root/'identity.json', IDENTITY)
        self.cli('parse-house-ptr', '--archive', evidence, '--metadata', pdf.with_suffix('.json'), '--output', extraction)
        self.cli('qualify-house-ptr', '--extraction', extraction, '--identity', root/'identity.json',
                 '--evidence-root', evidence, '--output', qualification)
        q = json.loads(qualification.read_bytes())
        self.write(review/'status/summary.json', {'parser_commit':'a'*40, 'pending_parse_count':0,
            'qualification_count':1, 'qualified_transaction_count':len(q['transactions']),
            'quarantined_row_count':len(q['quarantined']), 'evidence_count':1, 'extracted_count':1,'failure_count':0})
        self.write(root/'state.json', {'status':'ok','run_at':'2026-09-18T12:00:00Z',
            'counts':{'archived':1,'pending':0,'failed':0}})
        base = deepcopy(BASE)
        if prior:
            base['transactions'] = qualify_automatic(raw, IDENTITY)['transactions']
        self.write(root/'base.json', base)
        return {'root':root,'evidence':evidence,'review':review,'pdf':pdf,'extraction':extraction,
                'qualification':qualification,'q':q,'raw':raw,'base':base}

    def candidate(self, data, *, prior_path=None, expected=0, roots=True):
        root=data['root']
        args=['build-house-candidate','--review-root',data['review'],'--state-status',root/'state.json',
              '--base',prior_path or root/'base.json','--output',root/'candidate.json']
        if roots: args += ['--evidence-root',data['evidence']]
        done=self.cli(*args,expected=expected)
        return json.loads((root/'candidate.json').read_bytes()) if expected==0 else done

    def reader(self, candidate, scopes):
        from unison_snapshot.builder import build
        from unison_snapshot.disclosure_status import attach_disclosure_status
        from unison_snapshot.public_repo import PublicSnapshotRepository
        from test_public_repo import FakeTransport
        from test_disclosure_status import PROVENANCE
        bundle=build(candidate,generated_at='2026-09-18T12:00:00Z',allow_production=True)
        bundle=attach_disclosure_status(bundle,scopes,transactions=candidate['transactions'],provenance=PROVENANCE)
        public=PublicSnapshotRepository('contract','data',transport=FakeTransport(bundle.files))
        self.assertEqual(public.fetch().snapshot['transactions'],candidate['transactions'])
        self.assertIsNotNone(public.disclosure_status(public.resolve(),include_records=True))

    def test_normal_cli_status_reader_and_repeat(self):
        for kind,count in [('white',1),('white_dark',2),('white_black',2),('erase_black',2),('second_anchor_erased',1),('cross_white',1)]:
            with self.subTest(kind=kind):
                data=self.prepare(kind)
                candidate=self.candidate(data)
                self.assertEqual(len(candidate['transactions']),count)
                scopes=build_house_status(data['evidence'],data['review'],candidate['transactions'],[])
                record=scopes[0]['reports'][0]
                self.assertEqual(record['qualification_state'],'partial')
                self.assertEqual(record['qualified_transaction_count'],count)
                self.assertFalse(record['source_observation_coverage']['coverage_complete'])
                self.assertEqual(len(record['publication_transaction_ids']),count)
                self.reader(candidate,scopes)
                original=(data['root']/'candidate.json').read_bytes()
                self.candidate(data,prior_path=data['root']/'candidate.json')
                self.assertEqual((data['root']/'candidate.json').read_bytes(),original)
                self.assertEqual(build_house_status(data['evidence'],data['review'],candidate['transactions'],[]),scopes)

    def test_unknown_current_source_preserves_complete_old_formal_and_ids(self):
        data=self.prepare('white',prior=True)
        # The source-bound original old object is deliberately different from
        # a possible modern reclassification; visibility cannot migrate it.
        data['base']['transactions'][0]['instrument_type']='Bond'
        self.write(data['root']/'base.json',data['base'])
        candidate=self.candidate(data)
        self.assertEqual(candidate['transactions'],sorted(data['base']['transactions'],key=lambda r:r['id']))
        scopes=build_house_status(data['evidence'],data['review'],candidate['transactions'],[])
        record=scopes[0]['reports'][0]
        self.assertEqual(record['qualified_transaction_count'],1)
        self.assertEqual(len(record['publication_transaction_ids']),2)
        self.assertEqual(record['qualification_state'],'partial')
        self.reader(candidate,scopes)

    def test_visible_correction_of_old_duplicate_is_not_implicitly_authorized(self):
        data=self.prepare('white_black',prior=True)
        out=data['root']/'candidate.json';out.write_bytes(b'existing candidate')
        done=self.candidate(data,expected=1)
        self.assertIn('correction',done.stderr+done.stdout)
        self.assertEqual(out.read_bytes(),b'existing candidate')

    def test_missing_roots_resigned_source_and_changed_pdf_do_not_overwrite_output(self):
        data=self.prepare('white');self.candidate(data)
        out=data['root']/'candidate.json';original=out.read_bytes()
        self.candidate(data,expected=1,roots=False);self.assertEqual(out.read_bytes(),original)
        cached=data['extraction'].read_bytes();report=json.loads(cached)
        report['native_contrast_dispositions'][0]['status']='current_projected'
        from unison_snapshot.house_native_contrast import _digest
        report['native_contrast_binding']['observation_sha256']=_digest(report['native_contrast_observation'])
        self.write(data['extraction'],report)
        self.candidate(data,expected=1);self.assertEqual(out.read_bytes(),original)
        with self.assertRaises((ValueError,core.HouseIndexError)):
            build_house_status(data['evidence'],data['review'],json.loads(original)['transactions'],[])
        data['extraction'].write_bytes(cached)
        pdf=data['pdf'].read_bytes();data['pdf'].write_bytes(pdf+b'\n% source changed')
        self.candidate(data,expected=1);self.assertEqual(out.read_bytes(),original)
        with self.assertRaises((ValueError,core.HouseIndexError)):
            build_house_status(data['evidence'],data['review'],json.loads(original)['transactions'],[])
        data['pdf'].write_bytes(pdf)
        self.cli('qualify-house-ptr','--extraction',data['extraction'],'--identity',data['root']/'identity.json',
                 '--output',data['qualification'],expected=1)


if __name__=='__main__':
    unittest.main()
