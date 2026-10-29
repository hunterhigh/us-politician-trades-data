"""Explicit original-version selection through the normal candidate CLI.

Generated native sources test selection, not official accuracy. A second SHA
is a byte-comment version of the same PDF; selecting it does not authorize a
cross-version owner/classification/rekey migration or a publication delta.
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_oge_scoped_generic_cli import make_mixed_inputs
from test_oge_owner_correction import encoded
from unison_snapshot import __main__ as cli
from unison_snapshot.codec import encode
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.oge_reports import parse_archived_pdf


def add_source_version(data, document, original_sha):
    """Create an immutable same-document PDF version and real v2/v7 caches."""
    evidence = data['evidence_root']
    old_path = evidence / f'oge/reports/{document}/{original_sha}.pdf'
    raw = old_path.read_bytes() + b'\n% bounded explicit source-selection second version\n'
    sha = hashlib.sha256(raw).hexdigest()
    metadata = json.loads(old_path.with_suffix('.json').read_bytes())
    relative = f'oge/reports/{document}/{sha}.pdf'
    metadata.update(sha256=sha, byte_length=len(raw), archive_path=relative)
    path = evidence / relative
    path.write_bytes(raw)
    path.with_suffix('.json').write_bytes(encoded(metadata))
    # The generated controls are native-only. Fail instead of invoking OCR if
    # a future source-plan change routes this fixture to a recognizer.
    with patch('unison_snapshot.ocr_geometry.ocr_pdf_pages', side_effect=AssertionError('No OCR in source-selection tests')):
        for generic in (False, True):
            value = parse_archived_pdf(evidence, path.with_suffix('.json'), generic=generic)
            expected = 'oge-278t-pdf/v7' if generic else 'oge-278t-pdf/v2'
            if value['parser_version'] != expected:
                raise AssertionError('The source selection control requires an ordinary native v2/v7 pair')
            target = data['extractions_root'] / document / sha / (expected.replace('/', '-') + '.json')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded(value))
    return sha


def make_versioned_inputs(root):
    """Return the mixed two-document fixture plus A's second canonical SHA."""
    data = make_mixed_inputs(root)
    data['sha_a2'] = add_source_version(data, data['a'], data['sha_a'])
    return data


class OgeSourceSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-source-selection-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = make_versioned_inputs(self.root)
        self.output, self.audit = self.root / 'candidate.json', self.root / 'audit.json'

    def cache(self, doc, sha, version='v2'):
        return self.data['extractions_root'] / doc / sha / f'oge-278t-pdf-{version}.json'

    def flags(self, sha):
        return ['--document-source', f'{self.data["a"]}:{sha}']

    def run_cli(self, arguments=(), *, roots=True):
        args = ['snapshot', 'build-oge-candidate', '--catalog', str(self.data['catalog_path']),
            '--base', str(self.data['base_path']), '--extractions-dir', str(self.data['extractions_root']),
            '--data-cutoff-at', self.data['cutoff'], '--output', str(self.output), '--audit-output', str(self.audit)]
        if roots:
            args += ['--evidence-root', str(self.data['evidence_root'])]
        with patch.object(sys, 'argv', args + list(arguments)), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cli.main()

    def rejected(self, flags=(), *, roots=True):
        self.output.write_bytes(b'candidate sentinel')
        self.audit.write_bytes(b'audit sentinel')
        with self.assertRaises(SystemExit) as caught:
            self.run_cli(flags, roots=roots)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.output.read_bytes(), b'candidate sentinel')
        self.assertEqual(self.audit.read_bytes(), b'audit sentinel')

    def read(self):
        return json.loads(self.output.read_bytes()), json.loads(self.audit.read_bytes())

    def fingerprints(self):
        roots = (self.data['evidence_root'], self.data['extractions_root'])
        return {(str(root), path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
                for root in roots for path in root.rglob('*') if path.is_file()}

    def test_normal_selects_either_sha_keeps_other_document_and_repeats_next_base(self):
        original_base = self.data['base_path'].read_bytes()
        before = self.fingerprints()
        for selected in (self.data['sha_a'], self.data['sha_a2']):
            with self.subTest(selected=selected):
                self.data['base_path'].write_bytes(original_base)
                flags = self.flags(selected)
                self.run_cli(flags)
                candidate, audit = self.read()
                self.assertEqual(next(row for row in candidate['transactions'] if row['filing_id'] == self.data['b']),
                                 self.data['old_b'])
                report = next(row for row in audit['reports'] if row['document_id'] == self.data['a'])
                binding = report['source_interpretation_binding']
                raw = json.loads(self.cache(self.data['a'], selected).read_bytes())
                self.assertEqual(binding['source_sha256'], selected)
                self.assertEqual(binding['parser_version'], 'oge-278t-pdf/v2')
                self.assertEqual(binding['extraction_canonical_sha256'], hashlib.sha256(encode(raw)).hexdigest())
                self.assertEqual(candidate['people'], json.loads(original_base)['people'])
                first = self.output.read_bytes(), self.audit.read_bytes()
                self.run_cli(flags)
                self.assertEqual((self.output.read_bytes(), self.audit.read_bytes()), first)
                self.data['base_path'].write_bytes(encoded(candidate))
                self.run_cli(flags)
                repeated, repeated_audit = self.read()
                self.assertEqual(repeated['transactions'], candidate['transactions'])
                self.assertEqual(repeated['people'], candidate['people'])
                self.assertEqual(next(row for row in repeated_audit['reports']
                                      if row['document_id'] == self.data['a'])['source_interpretation_binding'], binding)
                self.data['review_root'].joinpath('oge/qualifications/current.json').parent.mkdir(parents=True, exist_ok=True)
                self.data['review_root'].joinpath('oge/qualifications/current.json').write_bytes(encoded(repeated_audit))
                scopes = build_oge_status(self.data['evidence_root'], self.data['review_root'], repeated['transactions'], [])
                record = next(row for scope in scopes if scope['scope_id'] == 'oge_direct_278t'
                              for row in scope['reports'] if row['report_id'] == self.data['a'])
                self.assertEqual(record['source_versions'], sorted([self.data['sha_a'], self.data['sha_a2']]))
                self.assertIn('multiple_archive_versions', record['reason_codes'])
                self.assertEqual(record['source_interpretation_binding'], binding)
                self.assertEqual(record['qualified_transaction_count'], 1)
                conflicting = deepcopy(repeated_audit)
                next(row for row in conflicting['reports'] if row['document_id'] == self.data['a'])['source_sha256'] = (
                    self.data['sha_a2'] if selected == self.data['sha_a'] else self.data['sha_a'])
                self.data['review_root'].joinpath('oge/qualifications/current.json').write_bytes(encoded(conflicting))
                with self.assertRaisesRegex(ValueError, 'source differs from its authenticated input selection'):
                    build_oge_status(self.data['evidence_root'], self.data['review_root'], repeated['transactions'], [])
        self.assertEqual(self.fingerprints(), before)

    def test_generic_document_uses_only_selected_sha_and_its_own_raw_v2_pair(self):
        selected = self.data['sha_a']
        flags = self.flags(selected) + ['--generic-document-id', self.data['a']]
        self.run_cli(flags)
        candidate, audit = self.read()
        a = next(row for row in candidate['transactions'] if row['filing_id'] == self.data['a'])
        self.assertEqual(a, {**self.data['old_a'], 'owner': 'Undisclosed'})
        self.assertEqual(next(row for row in candidate['transactions'] if row['filing_id'] == self.data['b']), self.data['old_b'])
        binding = next(row for row in audit['reports'] if row['document_id'] == self.data['a'])['source_interpretation_binding']
        self.assertEqual(binding['source_sha256'], selected)
        self.assertEqual(binding['parser_version'], 'oge-278t-pdf/v7')
        self.cache(self.data['a'], selected).unlink()
        # A's other-SHA v2 is deliberately still present, and cannot substitute.
        self.assertTrue(self.cache(self.data['a'], self.data['sha_a2']).is_file())
        self.rejected(flags)

    def test_invalid_duplicate_missing_pin_cache_root_and_source_fail_before_outputs(self):
        a, selected = self.data['a'], self.data['sha_a']
        pin = f'{a}:{selected}'
        for flags in ([], ['--document-source', pin.upper()], ['--document-source', '../outside'],
                      ['--document-source', pin, '--document-source', pin],
                      ['--document-source', pin, '--document-source', f'{a}:{self.data["sha_a2"]}'],
                      ['--document-source', '9'*32 + ':' + selected],
                      ['--document-source', a + ':' + '0'*64]):
            with self.subTest(flags=flags):
                self.rejected(flags)
        self.rejected(self.flags(selected), roots=False)
        cache = self.cache(a, selected)
        original = cache.read_bytes()
        cache.unlink()
        self.rejected(self.flags(selected))
        cache.write_bytes(original)
        for replacement in ({**json.loads(original), 'owner_scope_complete': True},
                            {**json.loads(original), 'source_sha256': self.data['sha_a2']}):
            cache.write_bytes(encoded(replacement))
            self.rejected(self.flags(selected))
        cache.write_bytes(original)
        misplaced = cache.with_name('not-canonical.json')
        cache.rename(misplaced)
        self.rejected(self.flags(selected))
        misplaced.rename(cache)
        source = self.data['evidence_root'] / f'oge/reports/{a}/{selected}.pdf'
        source_raw = source.read_bytes()
        source.write_bytes(source_raw + b' changed source')
        self.rejected(self.flags(selected))
        source.write_bytes(source_raw)
        add_source_version(self.data, self.data['b'], self.data['sha_b'])
        # Pinning A cannot silently choose one of B's two versions.
        self.rejected(self.flags(selected))

    def test_pin_parser_is_bounded_and_rejects_duplicate_document_identity(self):
        from unison_snapshot.oge import OgeCatalogError
        from unison_snapshot.oge_source_selection import parse_document_sources
        pin = f'{self.data["a"]}:{self.data["sha_a"]}'
        self.assertEqual(parse_document_sources([]), {})
        self.assertEqual(parse_document_sources([pin]), {self.data['a']: self.data['sha_a']})
        for values in ([pin, pin], [pin, f'{self.data["a"]}:{self.data["sha_a2"]}'],
                       [pin.upper()], [pin + ' '], [pin.replace(':', '/', 1)],
                       [f'{n:032x}:{n:064x}' for n in range(33)]):
            with self.subTest(values=values):
                with self.assertRaises(OgeCatalogError):
                    parse_document_sources(values)


if __name__ == '__main__':
    unittest.main()
