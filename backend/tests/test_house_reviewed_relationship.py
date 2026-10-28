from copy import deepcopy
from pathlib import Path
import unittest

from unison_snapshot.corrections import load_correction
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_candidate import _paper_pending_count
from unison_snapshot.house_ptr_fixed_source_core import canonical_sha256
from unison_snapshot.reviewed_house_paper import _validate_prior_relationship


class ReviewedHouseRelationshipTests(unittest.TestCase):
    def setUp(self):
        self.correction = load_correction(Path(__file__).resolve().parents[1] /
            'data/corrections/house_clerk/9116141.json')
        relation = self.correction['relationship_review']
        self.raw, self.corrected, self.slots = {}, {}, {}
        for row in relation['target_pending_rows']:
            target = f"p{row['page']}#{row['band']}"
            self.raw[target] = {'raw_fields': {'direction_mark': row['direction'], 'amount_letter': row['amount_letter']}}
            self.corrected[target] = {k: row[k] for k in ('asset_name','event_date','account_title')}
            self.slots[row['page'],row['band']] = {'cells': {
                'asset': {'crop_sha256':row['asset_crop_sha256']},
                'event_date': {'crop_sha256':row['event_crop_sha256']}}}
        self.prior = {'sha256':relation['prior_source_sha256'],
            'document_id':relation['prior_document_id'], 'filed_date':relation['prior_filed_date']}

    def validate(self, correction=None):
        correction = correction or self.correction
        _validate_prior_relationship(correction, self.raw, self.corrected, self.slots,
            list(self.raw), correction['document_id'], correction['source_sha256'], self.prior)

    def test_saved_nonoverlapping_relationship_can_release_only_reviewed_rows(self):
        self.validate()
        self.assertEqual(len(self.raw),15)
        self.assertEqual(self.correction['source_review']['physical_counts']['data_candidate'],134)

    def test_review_digest_and_target_inventory_cannot_be_substituted(self):
        changed = deepcopy(self.correction)
        changed['relationship_review']['target_pending_rows'].pop()
        with self.assertRaises(ValueError): self.validate(changed)
        # Even a newly saved review still needs the complete pending-row set.
        changed['assertions']['relationship_review_sha256'] = canonical_sha256(changed['relationship_review'])
        with self.assertRaisesRegex(ValueError,'inventory'): self.validate(changed)

    def test_field_and_crop_changes_do_not_gain_authority_from_review_flag(self):
        target = next(iter(self.corrected))
        for field in ('event_date','asset_name','account_title'):
            old = self.corrected[target][field]
            self.corrected[target][field] = 'changed'
            with self.assertRaises(ValueError): self.validate()
            self.corrected[target][field] = old
        cell = next(iter(self.slots.values()))['cells']['event_date']
        cell['crop_sha256'] = 'a'*64
        with self.assertRaises(ValueError): self.validate()

    def test_overlap_and_wrong_prior_original_block_relationship(self):
        changed = deepcopy(self.correction)
        changed['relationship_review'].update(prior_latest_data_event_date='2026-05-01',prior_data_event_month='2026-05')
        changed['assertions']['relationship_review_sha256'] = canonical_sha256(changed['relationship_review'])
        with self.assertRaisesRegex(ValueError,'nonoverlap'): self.validate(changed)
        self.prior['sha256'] = 'b'*64
        with self.assertRaises(ValueError): self.validate()

    def test_complete_paper_has_no_pending_projection(self):
        self.assertEqual(_paper_pending_count({'transactions':[{}]}),0)

    def test_partial_paper_projection_cannot_reenter_candidate(self):
        with self.assertRaises(HouseIndexError):
            _paper_pending_count({'transactions':[{'id':'already-qualified'}],
                'quarantined':[], 'pending_transaction_projection':{}})
