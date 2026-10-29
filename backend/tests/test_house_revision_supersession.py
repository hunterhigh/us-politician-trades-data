"""Fixed snapshot/selection contracts; mocked resolution grants no source proof."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_house_revision_source_context as context_fixture
from unison_snapshot.house_members import archive_members, parse_members
from unison_snapshot.house_revision_source_context import load_house_revision_source_context
from unison_snapshot.fact_policy.source_objects import InventoryError
import unison_snapshot.fact_policy.house_revision_supersession as verifier


MAIN, EVIDENCE, REVIEW = 'a' * 40, 'b' * 40, 'c' * 40
PERSON = context_fixture.PERSON


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class HouseRevisionSupersessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = context_fixture.HouseRevisionSourceContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.old_fact = deepcopy(self.fixture.qualifications['20000001']['transactions'][0])
        self.target = {**deepcopy(self.old_fact),
            'id': self.fixture.extractions['20000002']['transactions'][0]['extraction_id'],
            'filing_id': '20000002',
            'source_url': self.old_fact['source_url'].replace('20000001.pdf', '20000002.pdf'),
            'filed_at': '2026-09-15T00:00:00Z'}
        self.person = {'id': PERSON, 'name': 'Byron Donalds', 'chamber': 'House',
                       'source_identity': {'bioguide_id': 'D000032'}}
        self.old = {self.old_fact['id']: deepcopy(self.old_fact)}
        self.current = {self.target['id']: deepcopy(self.target)}
        self.board = {'transactions': [deepcopy(self.old_fact)], 'people': [deepcopy(self.person)]}
        self.objects = {}
        self.calls, self.contexts = [], []
        self.collect_roots()
        self.pin_board()

    def collect_roots(self):
        for ref, root in ((EVIDENCE, self.fixture.evidence), (REVIEW, self.fixture.review)):
            for path in root.rglob('*'):
                if path.is_file():
                    self.objects[(ref, path.relative_to(root).as_posix())] = path.read_bytes()

    def pin_board(self, *, is_demo=False):
        raw = encoded(self.board); digest = hashlib.sha256(raw).hexdigest()
        self.objects[(MAIN, f'board/{digest}.json')] = raw
        self.objects[(MAIN, 'manifest.json')] = encoded({'is_demo': is_demo, 'board': digest})
        return digest

    def read(self, repo, ref, path):
        self.calls.append((ref, path))
        value = self.objects[(ref, path)]
        if value is None:
            raise AssertionError('excluded/orphan bytes must not be fetched: ' + path)
        return value

    def paths(self, repo, ref, directory):
        self.assertEqual(directory, 'house_clerk')
        return sorted(path[len(directory) + 1:] for selected_ref, path in self.objects
                      if selected_ref == ref and path.startswith(directory + '/'))

    def resolution_contract_only(self, inputs, baseline, *, prior_transactions, evidence_root, review_root):
        """Observe real reconstructed context, then substitute only chain output."""
        context = load_house_revision_source_context(evidence_root, review_root,
            requested_person_ids=[PERSON], prior_transactions=prior_transactions,
            current_transactions=list(self.current.values()))
        self.contexts.append(context)
        if context.status != 'observed_context':
            raise InventoryError('snapshot contract has unknown source context: ' + ';'.join(context.reasons))
        self.assertEqual(len(inputs), 2)
        self.assertEqual(len(context.documents_for(PERSON)), 2)
        self.assertTrue(all(document.pdf_bytes for document in context.documents_for(PERSON)))
        return {'transactions': [deepcopy(self.target)], 'replacements': [{
            'prior_transaction_id': self.old_fact['id'], 'transaction_id': self.target['id'],
            'person_id': PERSON, 'source_sha256': self.fixture.metas['20000002']['sha256']}]}

    def verify(self, **overrides):
        arguments = dict(old=self.old, current=self.current, repo=Path(self.fixture.temp.name),
            evidence_commit=EVIDENCE, review_commit=REVIEW, main_commit=MAIN,
            previous_people=[deepcopy(self.person)], read_object=self.read, list_paths=self.paths)
        arguments.update(overrides)
        with patch.object(verifier, 'resolve_source_bound_house_revisions',
                          side_effect=self.resolution_contract_only) as resolver:
            result = verifier.verified_house_revision_supersessions(**arguments)
        return result, resolver

    def test_real_content_hash_and_complete_snapshot_are_checked_before_mocked_chain(self):
        before = deepcopy((self.old, self.current, self.objects))
        result, resolver = self.verify()
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual([(r['old_id'], r['new_id']) for r in result],
                         [(self.old_fact['id'], self.target['id'])])
        self.assertEqual((self.old, self.current, self.objects), before)
        digest = json.loads(self.objects[(MAIN, 'manifest.json')])['board']
        self.assertEqual(hashlib.sha256(self.objects[(MAIN, f'board/{digest}.json')]).hexdigest(), digest)
        self.assertFalse(self.contexts[0].scope_complete)

    def test_missing_or_symbolic_fixed_refs_fail_before_any_blob_read(self):
        for field in ('repo', 'main_commit', 'review_commit', 'evidence_commit'):
            for value in (None, 'main') if field != 'repo' else (None,):
                with self.subTest(field=field, value=value):
                    self.calls.clear()
                    with self.assertRaisesRegex(InventoryError, 'fixed main, review and evidence commits'):
                        self.verify(**{field: value})
                    self.assertEqual(self.calls, [])

    def test_content_addressed_board_bytes_and_official_manifest_are_independent_requirements(self):
        digest = self.pin_board()
        original = self.objects[(MAIN, f'board/{digest}.json')]
        self.objects[(MAIN, f'board/{digest}.json')] = original + b' '
        with self.assertRaisesRegex(InventoryError, 'board content hash mismatch'):
            self.verify()
        self.objects[(MAIN, f'board/{digest}.json')] = original
        self.pin_board(is_demo=True)
        with self.assertRaisesRegex(InventoryError, 'not an official snapshot'):
            self.verify()
        self.assertFalse(any(ref in (EVIDENCE, REVIEW) for ref, _ in self.calls))

    def test_all_old_fact_fields_and_all_person_fields_are_bound_to_fixed_main(self):
        for mutate in ('amount', 'optional', 'people'):
            with self.subTest(mutate=mutate):
                old = deepcopy(self.old); people = [deepcopy(self.person)]
                if mutate == 'amount': old[self.old_fact['id']]['amount_low'] += 1
                elif mutate == 'optional': old[self.old_fact['id']]['new_null_field'] = None
                else: people[0]['source_identity']['bioguide_id'] = 'D999999'
                self.calls.clear()
                with self.assertRaisesRegex(InventoryError, 'complete fact differs|previous person differs'):
                    self.verify(old=old, previous_people=people)
                self.assertFalse(any(ref in (EVIDENCE, REVIEW) for ref, _ in self.calls))

    def test_omitted_fixed_main_person_fact_or_duplicate_ID_is_not_silently_indexed(self):
        for kind in ('omitted_fact', 'duplicate_transaction', 'duplicate_person'):
            with self.subTest(kind=kind):
                self.board = {'transactions': [deepcopy(self.old_fact)], 'people': [deepcopy(self.person)]}
                if kind == 'omitted_fact':
                    self.board['transactions'].append({**self.old_fact, 'id': 'house-ptr:another-fixed-old'})
                elif kind == 'duplicate_transaction': self.board['transactions'].append(deepcopy(self.old_fact))
                else: self.board['people'].append(deepcopy(self.person))
                self.pin_board(); self.calls.clear()
                with self.assertRaisesRegex(InventoryError, 'omits fixed main person facts|invalid or duplicate ID'):
                    self.verify()
                self.assertFalse(any(ref in (EVIDENCE, REVIEW) for ref, _ in self.calls))

    def add_excluded_other_person(self):
        extra = (b"<member><statedistrict>NY01</statedistrict><member-info><bioguideID>C000001</bioguideID>"
            b"<official-name>Ada Carter</official-name><party>D</party><state postal-code='NY'/></member-info></member>")
        xml = context_fixture.XML.replace(b'</members>', extra + b'</members>')
        date, members = parse_members(xml)
        archive_members(self.fixture.evidence, xml, {}, members, date, retrieved_at=context_fixture.TIME)
        self.fixture.archive_index(self.fixture.rows + context_fixture.member('20000003',
            first='Ada', last='Carter', district='NY01'), '2026-09-19T00:00:00Z')
        self.collect_roots()
        excluded = []
        for ref, directory, suffix in ((EVIDENCE, 'documents', '.pdf'), (EVIDENCE, 'documents', '.json'),
                (REVIEW, 'extractions', '.json'), (REVIEW, 'qualifications', '.json')):
            path = f'house_clerk/{directory}/2026/20000003/' + 'd' * 64 + suffix
            self.objects[(ref, path)] = None  # The injected reader fails if any large excluded blob is requested.
            excluded.append((ref, path))
        return excluded

    def test_two_stage_XML_index_selection_never_fetches_other_person_PDF_or_caches(self):
        excluded = self.add_excluded_other_person()
        # Bound total snapshot bytes well below an imagined giant excluded PDF;
        # the guard must select paths first, not read then check their person.
        with patch.object(verifier, 'MAX_BYTES', 256 * 1024):
            result, _ = self.verify()
        self.assertEqual(len(result), 1)
        self.assertTrue(all(key not in self.calls for key in excluded))
        self.assertEqual(self.contexts[-1].for_person(PERSON)['excluded_sources'],
                         [{'key': '2026/20000003', 'person_id': 'house:C000001'}])
        for ref, root in ((EVIDENCE, self.fixture.evidence), (REVIEW, self.fixture.review)):
            selected = [p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
                        and '/20000001/' in p.as_posix()]
            self.assertTrue(all((ref, path) in self.calls for path in selected))

    def test_unindexed_PDF_and_review_only_orphans_remain_unknown_without_fetching_bytes(self):
        self.add_excluded_other_person()
        for directory, ref, suffix in (('documents', EVIDENCE, '.pdf'), ('extractions', REVIEW, '.json')):
            with self.subTest(directory=directory):
                orphan = f'house_clerk/{directory}/2026/29999999/' + 'e' * 64 + suffix
                self.objects[(ref, orphan)] = None
                self.calls.clear(); self.contexts.clear()
                with self.assertRaisesRegex(InventoryError, 'root_source_not_bound_to_any_archived_index'):
                    self.verify()
                self.assertNotIn((ref, orphan), self.calls)
                self.assertIn('2026/29999999', self.contexts[-1].provenance()['unindexed_root_source_keys'])
                del self.objects[(ref, orphan)]


if __name__ == '__main__':
    unittest.main()
