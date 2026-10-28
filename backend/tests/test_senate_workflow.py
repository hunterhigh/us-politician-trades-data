from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SenateWorkflowTests(unittest.TestCase):
    def test_every_candidate_rebuild_has_the_archived_evidence_root(self):
        calls = 0
        for name in ['senate.yml', 'replay.yml', 'senate-amendment-history-resolve.yml']:
            content = (ROOT / '.github/workflows' / name).read_text(encoding='utf-8')
            for step in content.split('\n      - '):
                if 'python -m unison_snapshot build-senate-candidate' not in step:
                    continue
                expected_binding = 'EVIDENCE_ROOT: ${{ steps.trees.outputs.evidence_root }}'
                self.assertIn(expected_binding, step)
                lines = step.splitlines()
                for i, line in enumerate(lines):
                    if 'python -m unison_snapshot build-senate-candidate' not in line:
                        continue
                    command = line
                    while lines[i].rstrip().endswith('\\'):
                        i += 1
                        command += '\n' + lines[i]
                    self.assertIn('--evidence-root "$EVIDENCE_ROOT"', command)
                    calls += 1
        self.assertEqual(calls, 3)
        activation = (ROOT / '.github/workflows/senate-amendment-history-resolve.yml').read_text(encoding='utf-8').split('  activate:', 1)[1]
        self.assertIn('git worktree add --detach "$evidence_root" origin/evidence', activation)
        self.assertIn('echo "evidence_root=$evidence_root" >> "$GITHUB_OUTPUT"', activation)
        primary = (ROOT / '.github/workflows/senate.yml').read_text(encoding='utf-8')
        self.assertIn('uses: ./.github/actions/data-trees', primary)
        self.assertNotIn('download-artifact', primary)
        self.assertNotIn('senate-roster-evidence', primary)


    def test_catalog_collection_is_double_gated_and_default_closed(self):
        content = (ROOT / ".github/workflows/senate.yml").read_text(encoding="utf-8")
        self.assertIn("SENATE_EFD_COLLECTION_ENABLED: ${{ vars.SENATE_EFD_COLLECTION_ENABLED }}", content)
        self.assertIn("SENATE_EFD_TERMS_ACKNOWLEDGED: ${{ vars.SENATE_EFD_TERMS_ACKNOWLEDGED }}", content)
        self.assertIn("id: gate", content)
        self.assertIn("steps.gate.outputs.status == 'enabled'", content)
        self.assertIn("python -m unison_snapshot senate-efd-gate", content)
        self.assertIn("python -m unison_snapshot discover-senate-efd", content)
        self.assertNotIn("curl ", content)
        self.assertNotIn("wget ", content)

    def test_gate_check_preserves_last_complete_source_state(self):
        content = (ROOT / ".github/workflows/senate.yml").read_text(
            encoding="utf-8")
        self.assertIn('senate-source-state gate', content)
        from unison_snapshot.commands.senate_source_state import record_gate
        import json, tempfile
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state=root/'state';run=root/'run'
            (state/'status').mkdir(parents=True);run.mkdir()
            prior={'status':'blocked','catalog':{'sha256':'a'*64},
                   'historical_roster':{'sha256':'b'*64},'reports':{'evidence_count':129}}
            (state/'status/senate_efd.json').write_text(json.dumps(prior))
            (run/'senate-efd-gate.json').write_text(json.dumps(
                {'status':'enabled','collection_enabled':True,'terms_acknowledged':True}))
            record_gate(state,run,'2026-10-29T00:00:00+00:00')
            saved=json.loads((state/'status/senate_efd.json').read_bytes())
            for key in ('status','catalog','reports','historical_roster'):
                self.assertEqual(saved[key],prior[key])

    def test_catalog_respects_evidence_review_and_state_boundaries(self):
        content = (ROOT / ".github/workflows/senate.yml").read_text(encoding="utf-8")
        self.assertIn("roles: evidence", content)
        self.assertIn("senate-source-state review", content)
        self.assertIn("senate-source-state collected", content)
        self.assertIn("python -m unison_snapshot match-senate-catalog", content)
        self.assertIn("python -m unison_snapshot archive-senate-report-entrypoints", content)
        self.assertIn("python -m unison_snapshot extract-senate-report-entrypoints", content)
        self.assertIn("scope: senate", content)
        self.assertNotIn("upload-artifact", content)
        self.assertIn("identity_roster_sha256", (ROOT / "backend/src/unison_snapshot/commands/senate_source_state.py").read_text(encoding="utf-8"))
        self.assertIn("status/senate_efd.json", content)
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("group: disclosure-source-writer", content)
        self.assertIn("'entrypoint_count': report_batch['archived_total']", (ROOT / "backend/src/unison_snapshot/commands/senate_source_state.py").read_text(encoding="utf-8"))
        self.assertIn("'report_evidence_count': evidence_count", (ROOT / "backend/src/unison_snapshot/commands/senate_source_state.py").read_text(encoding="utf-8"))
        self.assertIn("senate_efd/paper_report_failures/*/*/{parser_version}.json", (ROOT / "backend/src/unison_snapshot/commands/senate_source_state.py").read_text(encoding="utf-8"))
        self.assertIn(".local/senate-efd-state.json", content)
        self.assertIn("python -m unison_snapshot build-senate-candidate", content)
        self.assertIn("candidates/sources/senate_efd-current.json", content)
        self.assertIn("senate_efd/qualifications/", content)
        self.assertIn("python -m unison_snapshot assemble-source", content)
        self.assertIn("--html-output", content)

    def test_house_and_senate_runs_use_the_shared_assembly_contract(self):
        for name,source in [('house','house_clerk'),('senate','senate_efd')]:
            content=(ROOT/'.github/workflows'/f'{name}.yml').read_text(encoding='utf-8')
            self.assertIn('python -m unison_snapshot assemble-source',content)
            self.assertIn('--source '+source,content)
            self.assertIn('--base backend/data/empty-disclosure.json',content)
            self.assertIn('--html-output',content)
            self.assertNotIn('HEAD:refs/heads/main',content)

    def test_annual_source_uses_shared_assembly_without_claiming_new_collection(self):
        content=(ROOT/'.github/workflows/senate.yml').read_text(encoding='utf-8')
        annual=content.split('  annual:',1)[1].split('  roster:',1)[0]
        self.assertIn('python -m unison_snapshot senate-annual overlay',annual)
        self.assertIn('python -m unison_snapshot assemble-source',annual)
        self.assertIn('inputs.collect_annual == true',annual)
        self.assertIn('--source senate_efd',annual)
        self.assertNotIn('    concurrency:',content)
        self.assertNotIn('HEAD:refs/heads/main',annual)

    def test_roster_refresh_preserves_catalog_gate_and_status(self):
        content = (ROOT / ".github/workflows/senate.yml").read_text(encoding="utf-8")
        self.assertIn("gate = prior.get('gate'", content)
        self.assertIn("for key in ('catalog', 'reports')", content)
        self.assertIn("'collection_enabled': gate['collection_enabled']", content)
        self.assertIn("'terms_acknowledged': gate['terms_acknowledged']", content)

    def test_history_plan_archives_only_catalog_and_does_not_mutate_candidate_state(self):
        content = (ROOT / ".github/workflows/senate-amendment-history-plan.yml").read_text(
            encoding="utf-8")
        self.assertIn("python -m unison_snapshot plan-senate-amendment-backfill", content)
        self.assertIn("git -C \"$EVIDENCE_ROOT\" add senate_efd/catalog", content)
        self.assertIn("Require a unique predecessor for every planned target", content)
        self.assertNotIn("archive-senate-report-entrypoints", content)
        self.assertNotIn("HEAD:refs/heads/state", content)
        self.assertNotIn("HEAD:refs/heads/review", content)

    def test_history_resolution_is_bounded_and_activates_only_after_validation(self):
        content = (ROOT / ".github/workflows/senate-amendment-history-resolve.yml").read_text(
            encoding="utf-8")
        self.assertIn("--document-ids .local/senate-history-candidate-ids.json", content)
        self.assertIn("--limit 16", content)
        self.assertIn("counts.count(1) == 8 and counts.count(2) == 4", content)
        self.assertIn("resolve-senate-amendment-backfill", content)
        self.assertIn("activate-senate-amendment-supplement", content)
        self.assertIn("audit['input_transaction_count'] != 1646", content)
        self.assertIn("Historical predecessor escaped into the candidate", content)
        self.assertIn("git -C \"$EVIDENCE_ROOT\" add senate_efd/catalog senate_efd/reports", content)
        self.assertIn("HEAD:refs/heads/review", content)

    def test_saved_paper_replay_is_selected_and_uses_existing_official_inputs(self):
        content=(ROOT/'.github/workflows/replay.yml').read_text(encoding='utf-8')
        paper=content.split('  senate-paper:',1)[1].split('  whitehouse-pdf:',1)[0]
        self.assertIn("inputs.source == 'senate' && inputs.mode == 'paper-saved'",paper)
        self.assertIn('prepare-senate-paper-runtime',paper)
        self.assertIn('--tesseract __OCR_DISABLED__',paper)
        self.assertIn('build-senate-candidate',paper)
        self.assertIn('assemble-source',paper)
        self.assertIn('scope: senate',paper)
        self.assertNotIn('archive-senate-paper-pages',paper)
        self.assertNotIn('HEAD:refs/heads/state',paper)


if __name__ == '__main__':unittest.main()
