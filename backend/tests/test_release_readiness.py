import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.codec import digest, encode
from unison_snapshot.release_readiness import ReleaseReadinessError, validate_publication


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "status").mkdir()
        (self.root / "candidates" / "sources").mkdir(parents=True)
        self._write("status/house_clerk.json", {
            "schema_version": "house-review-run/v1", "evidence_count": 3,
            "extracted_count": 2, "failure_count": 1, "pending_parse_count": 0,
            "qualification_count": 2, "status_conflict_count": 0,
        })
        self._write("status/house_holdings.json", {
            "schema_version": "house-holding-run/v1", "evidence_count": 4,
            "current_member_annual_count": 4, "remaining_archive_count": 0,
            "extraction_count": 3, "failure_count": 1, "pending_parse_count": 0,
            "qualification_count": 3,
        })
        self._write("status/oge.json", {
            "schema_version": "oge-review-run/v1", "direct_pdf_count": 2,
            "archived_report_count": 2, "extraction_count": 2,
            "pending_direct_count": 0, "extraction_failure_count": 0,
            "request_required_count": 99, "qualified_transaction_count": 1,
            "qualified_holding_count": 0,
        })
        self._write("status/senate_efd.json", {
            "schema_version": "senate-review-run/v1", "catalog_record_count": 3,
            "report_entrypoint_count": 3, "report_entrypoint_pending_count": 0,
            "report_entrypoint_failure_count": 0, "report_evidence_count": 2,
            "report_extraction_failure_count": 1, "paper_report_pending_count": 0,
        })
        self.sources = {}
        for index, source_id in enumerate(("house_clerk", "oge", "senate_efd"), 1):
            source = {"meta": {"is_demo": False}, "people": [{"id": f"p{index}"}],
                      "transactions": [{"id": f"t{index}"}],
                      "reported_holdings": ([{"id": "h1"}] if source_id == "house_clerk" else []),
                      "security_market_data": [], "source_health": [{"source_id": source_id}]}
            self.sources[source_id] = source
            self._write(f"candidates/sources/{source_id}-current.json", source)
        self.candidate = {
            "meta": {"is_demo": False, "data_cutoff_at": "2026-09-20T23:59:59Z"},
            "people": [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}],
            "transactions": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}],
            "reported_holdings": [{"id": "h1"}], "security_market_data": [],
            "source_health": [{"source_id": "house_clerk"}, {"source_id": "oge"},
                              {"source_id": "senate_efd"}],
        }
        self.candidate_path = self.root / "candidates" / "disclosure-current.json"
        self._write("candidates/disclosure-current.json", self.candidate)
        self._write_cutoff()

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, relative: str, value: dict):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode(value))

    def _write_cutoff(self):
        self._write("status/disclosure_cutoff.json", {
            "schema_version": "disclosure-cutoff-audit/v1",
            "candidate_sha256": digest(encode(self.candidate)),
            "sources": {
                source_id: {
                    "candidate_sha256": digest(encode(source)),
                    "retained_counts": {
                        "transactions": len(source["transactions"]),
                        "reported_holdings": len(source["reported_holdings"]),
                    },
                }
                for source_id, source in self.sources.items()
            },
        })

    def _deferred_oge(self):
        document = 'a' * 32
        row = {'id': 't2', 'filing_id': document, 'source_id': 'oge',
               'verification_status': 'official_matched', 'asset_name': 'Original asset',
               'ticker': None, 'owner': 'Self', 'instrument_type': 'Bond'}
        self.sources['oge']['transactions'] = [row]
        self._write('candidates/sources/oge-current.json', self.sources['oge'])
        self.candidate['transactions'][1] = dict(row)
        self._write('candidates/disclosure-current.json', self.candidate)
        self._write_cutoff()
        status = json.loads((self.root/'status/oge.json').read_bytes())
        status.update(qualified_transaction_count=None, publication_transaction_count=1,
            oge_direct_publication_transaction_count=1, oge_direct_qualified_transaction_count=0,
            whitehouse_public_qualified_transaction_count=0,
            whitehouse_public_qualified_annual_transaction_count=0,
            pending_extraction_count=0, retained_prior_qualified_transaction_count=0)
        self._write('status/oge.json', status)
        audit = {'schema_version': 'oge-candidate-audit/v1', 'extraction_count': 2,
            'promoted_transaction_count': 1, 'current_qualified_transaction_count': 0,
            'reports': [{'document_id': document, 'promoted_count': 1,
                'native_classification': {'qualified_ids': [], 'deferred_ids': ['t2']}},
                {'document_id': 'b' * 32, 'promoted_count': 0}]}
        self._write('oge/qualifications/current.json', audit)
        self._write('previous-board.json', {'transactions': [dict(row)]})
        return status, audit

    def test_unchanged_deferred_fact_does_not_block_the_rest_of_publication(self):
        self._deferred_oge()
        before = self.candidate_path.read_bytes()
        result = validate_publication(self.root, self.candidate_path,
            previous_board_path=self.root/'previous-board.json')
        self.assertEqual(result['transaction_count'], 3)
        self.assertEqual(result['oge_publication_accounting'], {
            'publication_transaction_count': 1, 'current_qualified_transaction_count': 0,
            'historical_unchanged_transaction_count': 1})
        self.assertEqual(self.candidate_path.read_bytes(), before)
        self.assertIsNone(json.loads((self.root/'status/oge.json').read_bytes())['qualified_transaction_count'])

    def test_source_readiness_uses_the_fixed_published_git_board_when_no_path_is_supplied(self):
        import subprocess
        self._deferred_oge()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE)
        git('init', '-q')
        git('config', 'user.name', 'Readiness fixture')
        git('config', 'user.email', 'fixture@example.test')
        previous = (self.root/'previous-board.json').read_bytes()
        sha = digest(previous)
        self._write('manifest.json', {'is_demo': False, 'board': sha, 'snapshot_id': 'a' * 64})
        (self.root/'board').mkdir()
        (self.root/'board'/f'{sha}.json').write_bytes(previous)
        git('add', 'manifest.json', f'board/{sha}.json')
        git('commit', '-qm', 'Published fixture')
        git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        (self.root/'board'/f'{sha}.json').write_text('uncommitted wrong board', encoding='utf-8')
        result = validate_publication(self.root, self.candidate_path)
        self.assertEqual(result['oge_publication_accounting']['historical_unchanged_transaction_count'], 1)

    def test_deferred_fact_requires_full_previous_object_and_preserves_existing_bindings(self):
        self._deferred_oge()
        with self.assertRaisesRegex(ReleaseReadinessError, 'previous published board'):
            validate_publication(self.root, self.candidate_path)
        self.sources['oge']['transactions'][0]['asset_name'] = 'Unproved changed asset'
        self.candidate['transactions'][1]['asset_name'] = 'Unproved changed asset'
        self._write('candidates/sources/oge-current.json', self.sources['oge'])
        self._write('candidates/disclosure-current.json', self.candidate)
        self._write_cutoff()
        with self.assertRaisesRegex(ReleaseReadinessError, 'previous complete published object'):
            validate_publication(self.root, self.candidate_path,
                previous_board_path=self.root/'previous-board.json')

    def test_deferred_counts_and_ids_must_close_and_cannot_cover_old_parser_fallback(self):
        for change, message in (
            ({'publication_transaction_count': 2}, 'subtotals'),
            ({'oge_direct_qualified_transaction_count': 2}, 'subtotals'),
            ({'retained_prior_qualified_transaction_count': 1}, 'inconsistent source state'),
        ):
            with self.subTest(change=change):
                status, _ = self._deferred_oge()
                status.update(change); self._write('status/oge.json', status)
                with self.assertRaisesRegex(ReleaseReadinessError, message):
                    validate_publication(self.root, self.candidate_path,
                        previous_board_path=self.root/'previous-board.json')
        _, audit = self._deferred_oge()
        audit['reports'][0]['native_classification']['deferred_ids'].append('not-a-published-row')
        self._write('oge/qualifications/current.json', audit)
        with self.assertRaisesRegex(ReleaseReadinessError, 'do not conserve'):
            validate_publication(self.root, self.candidate_path,
                previous_board_path=self.root/'previous-board.json')

    def test_accepts_accounted_scope_and_excludes_form_201(self):
        result = validate_publication(self.root, self.candidate_path)
        self.assertEqual(result["transaction_count"], 3)
        self.assertEqual(result["reported_holding_count"], 1)
        self.assertEqual(result["oge_form_201_excluded_count"], 99)

    def test_native_cutoff_audit_keeps_newer_house_and_oge_rows_and_rejects_truncation(self):
        from unison_snapshot.disclosure_candidate import build_disclosure_candidate
        for index,(source,value) in enumerate(self.sources.items()):
            cutoff='2026-10-23T00:00:00Z' if source=='senate_efd' else '2026-10-30T00:00:00Z'
            value['meta']['data_cutoff_at']=cutoff
            value['people'][0]['disclosure_authority']=source
            value['source_health'][0].update(status='partial',data_cutoff_at=cutoff)
            for kind in ('transactions','reported_holdings'):
                for row in value[kind]:
                    row.update(source_id=source,person_id=value['people'][0]['id'],filed_at=cutoff)
            self._write(f'candidates/sources/{source}-current.json',value)
        base={'meta':{'is_demo':False},'people':[],'transactions':[],'reported_holdings':[],
              'source_health':[],'security_market_data':[]}
        audit={}
        candidate=build_disclosure_candidate(base,self.sources,harmonization_audit=audit)
        audit['candidate_sha256']=digest(encode(candidate))
        self._write('candidates/disclosure-current.json',candidate)
        self._write('status/disclosure_cutoff.json',audit)
        self.assertEqual(validate_publication(self.root,self.candidate_path)['transaction_count'],3)
        audit['sources']['oge']['filtered_transaction_count']=1
        self._write('status/disclosure_cutoff.json',audit)
        with self.assertRaisesRegex(ReleaseReadinessError,'native source cutoffs or facts changed'):
            validate_publication(self.root,self.candidate_path)

    def test_accepts_declared_queues_without_changing_candidate_facts(self):
        value=json.loads((self.root/'status/house_holdings.json').read_bytes())
        value['remaining_archive_count']=1;value['current_member_annual_count']+=1
        self._write('status/house_holdings.json',value)
        before=self.candidate_path.read_bytes()
        result=validate_publication(self.root,self.candidate_path)
        self.assertEqual(result['pending']['house_annual_archive'],1)
        self.assertEqual(self.candidate_path.read_bytes(),before)

    def test_oge_failed_report_is_an_explicit_disposition_and_missing_report_still_fails(self):
        value=json.loads((self.root/'status/oge.json').read_bytes())
        value['extraction_count']=1;value['extraction_failure_count']=1
        self._write('status/oge.json',value)
        self.assertEqual(validate_publication(self.root,self.candidate_path)['oge_extraction_failure_count'],1)
        value['extraction_failure_count']=0;self._write('status/oge.json',value)
        with self.assertRaisesRegex(ReleaseReadinessError,'not fully accounted'):
            validate_publication(self.root,self.candidate_path)

    def test_rejects_unaccounted_senate_paper_report(self):
        value = json.loads((self.root / "status/senate_efd.json").read_text())
        value["report_evidence_count"] = 1
        self._write("status/senate_efd.json", value)
        with self.assertRaisesRegex(ReleaseReadinessError, "not fully accounted"):
            validate_publication(self.root, self.candidate_path)

    def test_rejects_stale_unified_candidate(self):
        self.candidate["transactions"].append({"id": "tampered"})
        self._write("candidates/disclosure-current.json", self.candidate)
        with self.assertRaisesRegex(ReleaseReadinessError, "does not match its cutoff audit"):
            validate_publication(self.root, self.candidate_path)

    def test_rebuilt_candidate_uses_its_own_audit_without_weakening_bindings(self):
        # A producer policy change changes the snapshot identity even when
        # source facts are unchanged. Review's older assembly audit stays frozen.
        old_audit = (self.root / 'status/disclosure_cutoff.json').read_bytes()
        self.candidate['meta']['snapshot_id'] = 'new-publication-policy'
        self._write('candidates/disclosure-current.json', self.candidate)
        with self.assertRaisesRegex(ReleaseReadinessError, 'does not match its cutoff audit'):
            validate_publication(self.root, self.candidate_path)
        audit = json.loads(old_audit)
        audit['candidate_sha256'] = digest(encode(self.candidate))
        self._write('governed-cutoff.json', audit)
        audit_path = self.root / 'governed-cutoff.json'
        self.assertEqual(validate_publication(self.root, self.candidate_path,
                         cutoff_audit_path=audit_path)['transaction_count'], 3)
        self.assertEqual((self.root / 'status/disclosure_cutoff.json').read_bytes(), old_audit)
        self.sources['house_clerk']['transactions'][0]['id'] = 'changed-source'
        self._write('candidates/sources/house_clerk-current.json', self.sources['house_clerk'])
        with self.assertRaisesRegex(ReleaseReadinessError, 'source candidate does not match cutoff audit'):
            validate_publication(self.root, self.candidate_path, cutoff_audit_path=audit_path)
        self.candidate['transactions'].pop()
        self._write('candidates/disclosure-current.json', self.candidate)
        with self.assertRaisesRegex(ReleaseReadinessError, 'does not match its cutoff audit'):
            validate_publication(self.root, self.candidate_path, cutoff_audit_path=audit_path)

    def test_staged_source_cannot_publish_against_old_unified_audit(self):
        source = json.loads((self.root / "candidates/sources/senate_efd-current.json")
                            .read_text(encoding="utf-8"))
        source["meta"]["data_cutoff_at"] = "2026-09-28T00:00:00Z"
        self._write("candidates/sources/senate_efd-current.json", source)
        with self.assertRaisesRegex(ReleaseReadinessError,
                                    "source candidate does not match cutoff audit"):
            validate_publication(self.root, self.candidate_path)

    def test_rejects_oge_candidate_that_omits_reported_holdings(self):
        status = json.loads((self.root / "status/oge.json").read_text())
        status["qualified_holding_count"] = 1
        self._write("status/oge.json", status)
        with self.assertRaisesRegex(ReleaseReadinessError,
                                    "holding count does not match its source candidate"):
            validate_publication(self.root, self.candidate_path)


if __name__ == "__main__":
    unittest.main()
