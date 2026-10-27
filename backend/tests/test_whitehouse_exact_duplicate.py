"""Same-original/native parser source controls, not official accuracy gold.

These tests use the actual two normal producers' distinct stable IDs. No
saved binding/receipt or equal core-field subset is treated as source proof.
The normal CLI/fixed-Git event tests live in the separate consumption module.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from unison_snapshot.whitehouse_exact_duplicate import (
    ExactDuplicateSources, POLICY_VERSION, prepare_whitehouse_exact_duplicate_sources,
)
from unison_snapshot.whitehouse_native_correction import NativeCorrectionError, _Sources
from whitehouse_exact_duplicate_fixture import make_exact_duplicate_inputs


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':')).encode()


@contextmanager
def replaced(path, raw):
    path = Path(path)
    previous = path.read_bytes()
    try:
        path.write_bytes(raw)
        yield
    finally:
        path.write_bytes(previous)


class WhiteHouseExactDuplicateSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='wh-exact-source-tests-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.data = make_exact_duplicate_inputs(Path(cls.temporary.name))

    def prepare(self, data=None, **kwargs):
        data = data or self.data
        options = {'evidence_root': data['evidence_root'], 'review_root': data['review_root']}
        options.update(kwargs)
        return prepare_whitehouse_exact_duplicate_sources(data['base'], data['extractions'],
                                                          data['source_bindings'], **options)

    def resolve(self, context, data=None):
        data = data or self.data
        report = data['extractions'][0]
        return context.resolve(report, report['transactions'][0],
            canonical_fact=data['canonical_fact'], duplicate_fact=data['duplicate_fact'])

    def test_real_two_producers_complete_objects_and_canonical_only_repeat(self):
        data = self.data
        self.assertNotEqual(data['canonical_fact']['id'], data['duplicate_fact']['id'])
        legacy_path = (data['review_root'] / 'oge/extractions' /
            data['oge_archive']['document_id'] / data['source_sha256'] / 'oge-278t-pdf-v2.json')
        legacy = json.loads(legacy_path.read_bytes())
        # The raw v2 reader cannot assign this cover's date. It is a source
        # cell/old-ID witness here, not the qualified formal date authority.
        self.assertIsNone(legacy['filed_at'])
        self.assertFalse(legacy['evidence_complete'])
        self.assertIn('filer_signature_date_not_unique', legacy['document_reasons'])
        before = encoded({key: data[key] for key in
                          ('base', 'extractions', 'source_bindings', 'canonical_fact', 'duplicate_fact')})
        original = data['source'].read_bytes()
        context = self.prepare()
        proof = self.resolve(context)
        self.assertIsNotNone(proof)
        self.assertEqual(proof['canonical_fact'], data['canonical_fact'])
        self.assertEqual(proof['duplicate_fact'], data['duplicate_fact'])
        self.assertEqual(proof['record']['source_proof_rule'], POLICY_VERSION)
        self.assertEqual(proof['record']['source_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(proof['record']['old_fact_sha256'], hashlib.sha256(encoded(data['duplicate_fact'])).hexdigest())
        self.assertFalse(context.pending_ids)
        # A caller cannot transplant this runtime proof to an edited report,
        # another row, or a partially equal formal object.
        report = deepcopy(data['extractions'][0]); report['unverified'] = True
        self.assertIsNone(context.resolve(report, report['transactions'][0],
            canonical_fact=data['canonical_fact'], duplicate_fact=data['duplicate_fact']))
        wrong = {**data['canonical_fact'], 'amount_low': 1002}
        self.assertIsNone(context.resolve(data['extractions'][0], data['extractions'][0]['transactions'][0],
            canonical_fact=wrong, duplicate_fact=data['duplicate_fact']))
        # The repeat still authenticates the same dependency set, but does not
        # manufacture a second removal or infer another discarded old object.
        repeat = deepcopy(data)
        repeat['base']['transactions'] = [deepcopy(data['canonical_fact'])]
        repeated = self.prepare(repeat)
        self.assertIsNone(self.resolve(repeated))
        self.assertEqual(repeated.provenance(), context.provenance())
        self.assertFalse(repeated.pending_ids)
        self.assertEqual(before, encoded({key: data[key] for key in
                         ('base', 'extractions', 'source_bindings', 'canonical_fact', 'duplicate_fact')}))
        self.assertEqual(original, data['source'].read_bytes())

    def test_full_old_projection_and_only_known_null_omission(self):
        for field, value in [('instrument_type', 'Bond'), ('amount_low', 1002),
                             ('option_type', 'call'), ('unknown_optional', None)]:
            data = deepcopy(self.data)
            # Equal wrong values on both sides cannot self-certify the pair.
            for fact in data['base']['transactions']:
                fact[field] = value
            data['canonical_fact'][field] = value
            data['duplicate_fact'][field] = value
            with self.subTest(field=field):
                context = self.prepare(data)
                self.assertIsNone(self.resolve(context, data))
                self.assertIn(data['duplicate_fact']['id'], context.pending_ids)
        # Known source-proved null omission may differ between the two shards;
        # the returned canonical still has its actual old key representation.
        data = deepcopy(self.data)
        for key in ('option_type', 'strike_price', 'expiration_date'):
            data['canonical_fact'].pop(key)
        for key in ('position_effect_basis', 'option_type'):
            data['duplicate_fact'].pop(key)
        data['base']['transactions'] = [deepcopy(data['canonical_fact']), deepcopy(data['duplicate_fact'])]
        proof = self.resolve(self.prepare(data), data)
        self.assertIsNotNone(proof)
        self.assertEqual(proof['canonical_fact'], data['canonical_fact'])
        self.assertEqual(proof['duplicate_fact'], data['duplicate_fact'])

    def test_no_actual_canonical_and_rootless_do_not_authorize_a_removal(self):
        data = deepcopy(self.data)
        data['base']['transactions'] = [deepcopy(data['duplicate_fact'])]
        data['extractions'].extend({**deepcopy(data['extractions'][0]),
            'document_id': 'wh-url:' + f'{index:024x}'} for index in range(20))
        # The unrelated new-source/WH-only path does not acquire this narrow
        # duplicate mechanism's additional original-file requirements.
        with patch('unison_snapshot.whitehouse_exact_duplicate._RootSources',
                   side_effect=AssertionError('no canonical: do not read source')):
            context = self.prepare(data, evidence_root='missing', review_root='missing')
        self.assertIsNone(self.resolve(context, data))
        context = self.prepare(evidence_root=None, review_root=None)
        self.assertIsNone(self.resolve(context))
        self.assertIn(self.data['duplicate_fact']['id'], context.pending_ids)
        self.assertIsNone(self.resolve(ExactDuplicateSources()))

    def test_source_cache_and_metadata_tampering_is_strict_not_pending(self):
        data = self.data
        sha, doc = data['source_sha256'], data['oge_archive']['document_id']
        direct_v7 = data['review_root'] / f'oge/extractions/{doc}/{sha}/oge-278t-pdf-v7.json'
        direct_v2 = direct_v7.with_name('oge-278t-pdf-v2.json')
        for path, change in [
                (direct_v2, lambda value: value['transactions'][0].update(amount_low=1002)),
                (direct_v7, lambda value: value.update(source_complete=True)),
                (data['source'].with_suffix('.json'), lambda value: value.update(position_title='Forged role'))]:
            value = json.loads(path.read_bytes()); change(value)
            variant = deepcopy(data)
            if path == direct_v7:
                # Even a freshly re-signed whole binding must not turn the
                # edited v7 JSON into an independently replayed observation.
                variant['source_bindings']['reports'][0]['oge_extraction'] = value
            with self.subTest(path=path.name), replaced(path, encoded(value)):
                with self.assertRaises((NativeCorrectionError, ValueError)):
                    self.prepare(variant)
        with replaced(data['source'], data['source'].read_bytes() + b'\nchanged'):
            with self.assertRaises(NativeCorrectionError):
                self.prepare()
        wh_v5 = next((data['review_root'] / 'whitehouse/extractions').rglob('*native-roles-v5.json'))
        value = json.loads(wh_v5.read_bytes()); value['unverified_cache_flag'] = True
        variant = deepcopy(data); variant['extractions'] = [value]
        with replaced(wh_v5, encoded(value)), self.assertRaises(NativeCorrectionError):
            self.prepare(variant)

    def test_actual_selected_inventory_paths_and_bounded_reader_injection(self):
        data = self.data
        review = data['review_root']
        coverage = review / 'whitehouse/coverage-selected.json'
        status = review / 'whitehouse/status-selected.json'
        coverage.write_bytes((review / 'whitehouse/coverage-current.json').read_bytes())
        status.write_bytes((review / 'whitehouse/extraction-status.json').read_bytes())
        try:
            with replaced(review / 'whitehouse/coverage-current.json', b'{}'), \
                    replaced(review / 'whitehouse/extraction-status.json', b'{}'):
                context = self.prepare(coverage_input=coverage, extraction_status_input=status)
                self.assertIsNotNone(self.resolve(context))
            files = {role: {path.relative_to(root).as_posix(): path.read_bytes()
                            for path in root.rglob('*') if path.is_file()}
                     for role, root in [('evidence', data['evidence_root']), ('review', review)]}
            reader = _Sources('unused', lambda repo, role, path: files[role][path])
            context = self.prepare(_sources=reader, evidence_root=None, review_root=None)
            self.assertIsNotNone(self.resolve(context))
            tiny = _Sources('unused', lambda repo, role, path: files[role][path], max_total=1)
            with self.assertRaisesRegex(NativeCorrectionError, 'byte budget'):
                self.prepare(_sources=tiny, evidence_root=None, review_root=None)
        finally:
            coverage.unlink(); status.unlink()

    def test_person_source_fields_must_match_but_annotations_are_preserved(self):
        for field, value in [('role', 'Forged role'), ('party', 'Forged party'),
                             ('state', 'ZZ'), ('chamber', 'Senate')]:
            data = deepcopy(self.data); data['base']['people'][0][field] = value
            with self.subTest(field=field):
                self.assertIsNone(self.resolve(self.prepare(data), data))
        data = deepcopy(self.data)
        data['base']['people'][0].update(portrait_url='https://example.invalid/portrait.png',
                                         priority=True, priority_reason='Existing annotation')
        original = deepcopy(data['base']['people'])
        self.assertIsNotNone(self.resolve(self.prepare(data), data))
        self.assertEqual(data['base']['people'], original)


if __name__ == '__main__':
    unittest.main()
