"""Missing raw dates conserve old facts without constructing a guessed full H."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf

from test_oge_owner_correction import fixture, write_files, encoded, CUTOFF, DOCUMENT
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.disclosure_status_oge import build_oge_status
from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.oge_native_owner_scope import (
    prepare_native_owner_scope, _identity_key, _person_id,
)
from unison_snapshot.whitehouse_native_correction import _historical_projection


class MissingRawFilingDateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, _, _, evidence, _ = fixture(heading=' ', extra_rows=[
            ['', 'Filer assets', '', '', '', ''],
            ['2', 'EXPLICIT Fund', 'Purchase', '06/02/2026', 'No', '$15,001 - $50,000'],
        ])
        cls.record = json.loads(next(raw for path, raw in evidence.items()
                                     if '/catalog/normalized/' in path))['transactions'][0]
        pdfpath = next(path for path in evidence if path.endswith('.pdf'))
        pdf = pymupdf.open(stream=evidence[pdfpath], filetype='pdf')
        # Change actual cover bytes before hashing and ordinary parser selection.
        # No extraction cache is patched to manufacture a null date.
        for xref in pdf[0].get_contents():
            content = pdf.xref_stream(xref)
            for day in (13, 14, 15, 16):
                content = content.replace(f'06/{day}/2026'.encode(), b'unreadable')
            pdf.update_stream(xref, content)
        raw = pdf.tobytes()
        pdf.close()
        sha = hashlib.sha256(raw).hexdigest()
        cls.relative = f"oge/reports/{cls.record['source_document_id']}/{sha}.pdf"
        metadata = json.loads(evidence[pdfpath[:-4] + '.json'])
        metadata.update(sha256=sha, byte_length=len(raw), archive_path=cls.relative)
        cls.files = {cls.relative: raw, cls.relative[:-4] + '.json': json.dumps(metadata).encode()}

    def test_actual_null_raw_date_defers_complete_dated_predecessors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_files(root, self.files)
            path = root / (self.relative[:-4] + '.json')
            legacy = parse_archived_pdf(root, path)
            native = parse_archived_pdf(root, path, generic=True)
            self.assertEqual((legacy['parser_version'], native['parser_version']),
                             ('oge-278t-pdf/v2', 'oge-278t-pdf/v7'))
            self.assertIsNone(legacy['filed_at'])
            self.assertIsNone(native['filed_at'])
            # This date represents an already published fact. It is deliberately
            # absent from both submitted raw interpretations and never donated
            # back to the new source projection.
            historical_context = {**legacy, 'filed_at': '2026-06-13'}
            pid = _person_id(_identity_key(self.record))
            old = _historical_projection(historical_context, legacy['transactions'][0], pid)
            explicit = _historical_projection(historical_context, legacy['transactions'][1], pid)
            facts = [old, explicit]
            before = deepcopy(facts)
            for evidence_root in (root, None):
                with self.subTest(original_available=evidence_root is not None):
                    context = prepare_native_owner_scope(native, legacy, self.record,
                                                         evidence_root=evidence_root)
                    self.assertEqual(context.decision(old), {'status': 'deferred', 'matched': False})
                    successor = {**old, 'owner': 'Undisclosed'}
                    self.assertEqual(context.decision(successor), {'status': 'deferred', 'matched': False})
                    self.assertIsNone(context.alias(successor))
                    self.assertFalse(context.ordinary_legacy_current(explicit))
                    view = context.ordinary_native_view(facts)
                    self.assertEqual(view['current_ids'], [])
                    self.assertEqual(set(view['published_ids']), {old['id'], explicit['id']})
                    self.assertEqual(set(view['deferred_ids']), {old['id'], explicit['id']})
                    self.assertEqual(facts, before)


class SourceMetadataIdentityTests(unittest.TestCase):
    def test_existing_catalog_flags_have_the_same_authentication_in_both_consumers(self):
        _, _, review, evidence, previous = fixture(heading=' ')
        catalog = json.loads(next(raw for path, raw in evidence.items()
                                  if '/catalog/normalized/' in path))
        legacy = json.loads(next(raw for path, raw in review.items() if path.endswith('v2.json')))
        native = json.loads(next(raw for path, raw in review.items() if path.endswith('v7.json')))
        metadata_path = next(path for path in evidence if '/reports/' in path and path.endswith('.json'))
        for field, value in (('amended_label', 'Amended'), ('pending_final_oge_disposition', True)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                eroot, rroot = root / 'evidence', root / 'review'
                files = deepcopy(evidence)
                metadata = json.loads(files[metadata_path])
                self.assertNotEqual(metadata[field], value)
                metadata[field] = value
                files[metadata_path] = encoded(metadata)
                write_files(eroot, files)
                write_files(rroot, review)
                with self.assertRaisesRegex(OgeCatalogError, 'source authentication differs'):
                    build_oge_candidate(catalog, [native], previous, data_cutoff_at=CUTOFF,
                        evidence_root=eroot, legacy_extractions_by_document={DOCUMENT: legacy})
                with self.assertRaisesRegex(ValueError, 'metadata differs from catalog identity'):
                    build_oge_status(eroot, rroot, previous['transactions'], [])


if __name__ == '__main__':
    unittest.main()
