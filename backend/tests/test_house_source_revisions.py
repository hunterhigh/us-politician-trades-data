"""Composition contracts; mocked scopes do not certify any PDF or OCR input."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from test_house_automatic_revisions import source
from unison_snapshot.house_revision_scope_guard import HouseRevisionScopeGuard
from unison_snapshot.house_source_revisions import resolve_source_bound_house_revisions


class HouseSourceRevisionTests(unittest.TestCase):
    def setUp(self):
        self.inputs = [source(), source('20000002', 'b' * 64, 'Amended',
                                       '2026-09-15', high='$2,000,000')]
        self.prior = self.inputs[0][0]['transactions']
        self.person = self.prior[0]['person_id']

    def test_missing_actual_source_context_retains_complete_prior_and_pending_amendment(self):
        inputs, prior = deepcopy(self.inputs), deepcopy(self.prior)
        result = resolve_source_bound_house_revisions(inputs, prior, prior_transactions=prior)
        self.assertEqual(result['transactions'], prior)
        self.assertEqual(result['replacements'], [])
        self.assertEqual(result['qualifications']['20000002'], inputs[1][0])
        observation = result['dispositions']['20000002'][0]
        self.assertEqual(observation['disposition'], 'revision_original_source_scope_unproven')
        self.assertEqual(observation['action'], 'retain_prior')
        self.assertNotIn('replacement', observation)
        self.assertEqual(self.inputs, inputs); self.assertEqual(self.prior, prior)
        self.assertEqual(resolve_source_bound_house_revisions(inputs, result['transactions'],
                         prior_transactions=prior), result)

    def test_unknown_person_keeps_independent_new_facts_and_previous_full_objects(self):
        other = source('20000003', 'c' * 64)
        current = deepcopy(self.prior + other[0]['transactions'])
        historical = deepcopy(self.prior)
        historical[0]['source_note'] = 'preserve this complete historical field'
        result = resolve_source_bound_house_revisions(self.inputs + [other], current,
                                                      prior_transactions=historical)
        self.assertEqual(result['transactions'], sorted(historical + other[0]['transactions'], key=lambda r:r['id']))
        self.assertFalse(result['replacements'])

    def test_proved_scope_still_requires_complete_formal_fact_and_unique_chronology(self):
        fake_scope = HouseRevisionScopeGuard({'allowed_groups': [[self.person, '2000140446']]})
        with patch('unison_snapshot.house_revision_scope_guard.derive_house_revision_scope', return_value=fake_scope):
            # This tests chain composition only; fake_scope is never a runtime
            # argument, a saved source proof or a source-profile positive.
            result = resolve_source_bound_house_revisions(self.inputs, self.prior, prior_transactions=self.prior)
            self.assertEqual(len(result['replacements']), 1)
            self.assertEqual(result['transactions'][0]['amount_high'], 2_000_000)
            changed = deepcopy(self.prior); changed[0]['amount_high'] = 123
            blocked = resolve_source_bound_house_revisions(self.inputs, self.prior, prior_transactions=changed)
            self.assertEqual(blocked['transactions'], changed)
            self.assertEqual(blocked['replacements'], [])

    def test_chain_cannot_escape_guard_official_ID_scope(self):
        fake_scope = HouseRevisionScopeGuard({'allowed_groups': [[self.person, '9999999999']]})
        with patch('unison_snapshot.house_revision_scope_guard.derive_house_revision_scope', return_value=fake_scope):
            with self.assertRaisesRegex(ValueError, 'escapes'):
                resolve_source_bound_house_revisions(self.inputs, self.prior, prior_transactions=self.prior)

    def test_unknown_scope_preserves_ordinary_paper_qualification_and_unknown_slots(self):
        # Reuse the existing paper interpreter's isolated source-cell contract;
        # this tests wrapper compatibility, not original-image OCR accuracy.
        from test_house_ptr_paper_qualification import HousePaperQualificationTests
        from unison_snapshot.house_ptr_paper_qualification import materialize_paper_observation, paper_materialization_counts
        from unison_snapshot.house_ptr import qualify_automatic
        fixture = HousePaperQualificationTests()
        fixture.setUp()
        raw, header, reread, slots = fixture._source_bound_fixture()
        with (patch('unison_snapshot.house_ptr_fixed_source_core.check_source_cells', return_value=slots),
              patch('unison_snapshot.house_ptr_paper_qualification._asset_reading_is_bound', return_value=True)):
            paper = materialize_paper_observation(raw, fixture.observation, fixture.metadata,
                fixture.identity, amount_header=header, field_reread=reread)
        inputs = []
        for _, extraction in self.inputs:
            extraction = deepcopy(extraction)
            extraction['source'].update(filer_name=fixture.metadata['filer_name'],
                                        state_district=fixture.metadata['state_district'])
            identity = {**fixture.identity, 'document_id': extraction['source']['document_id']}
            inputs.append((qualify_automatic(extraction, identity), extraction))
        prior = deepcopy(inputs[0][0]['transactions'])
        current = deepcopy(prior + paper['transactions'])
        snapshot = deepcopy(paper)
        result = resolve_source_bound_house_revisions([*inputs, (paper, paper['materialized_extraction'])],
            current, prior_transactions=prior)
        returned = result['qualifications'][fixture.metadata['document_id']]
        self.assertEqual(returned, paper)
        counts = paper_materialization_counts(returned, paper['materialized_extraction'])
        self.assertEqual(counts, paper_materialization_counts(paper, paper['materialized_extraction']))
        self.assertEqual(counts['unresolved_physical_slot_count'], 1)
        self.assertIsNone(counts['whole_source_unique_quarantined_row_count'])
        self.assertIn(paper['transactions'][0], result['transactions'])
        self.assertEqual(paper, snapshot)
        self.assertEqual(result['replacements'], [])

    def test_unknown_scope_keeps_full_legacy_id_and_does_not_leak_same_filing_raw_id(self):
        historical = deepcopy(self.prior)
        historical[0]['id'] = 'legacy-house:formal-row-17'
        historical[0]['source_note'] = 'complete older formal object'
        result = resolve_source_bound_house_revisions(self.inputs, deepcopy(self.prior),
            prior_transactions=historical)
        self.assertEqual(result['transactions'], historical)
        self.assertNotIn(self.prior[0]['id'], {row['id'] for row in result['transactions']})
        self.assertEqual(result['replacements'], [])

    def test_unproved_cached_promotion_cannot_enter_current_facts_or_status_qualification(self):
        from unison_snapshot.house_ptr import qualify_automatic
        qualification, extraction = self.inputs[1]
        ids = frozenset(r['extraction_id'] for r in extraction['transactions'])
        promoted = qualify_automatic(extraction, qualification['identity'], _source_bound_revision_ids=ids)
        self.assertEqual(len(promoted['transactions']), 1)
        result = resolve_source_bound_house_revisions([self.inputs[0], (promoted, extraction)],
            self.prior + promoted['transactions'], prior_transactions=self.prior)
        self.assertEqual(result['transactions'], self.prior)
        self.assertEqual(result['qualifications']['20000002'], qualification)
        self.assertEqual(result['qualifications']['20000002']['transactions'], [])
        self.assertEqual(result['replacements'], [])


if __name__ == '__main__':
    unittest.main()
