"""Actual catalog archive formats and fixed Git reads, without qualification."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_oge import row, payload, DIRECT
from test_oge_reports import Client
from unison_snapshot.oge import archive_catalog, parse_catalog_page, build_catalog, OgeSourceConfig
from unison_snapshot.oge_reports import archive_direct_pdf
from unison_snapshot.pipeline_qa.migration_inventory import git_object
from unison_snapshot.pipeline_qa.whitehouse_owner_correction import _Sources
import unison_snapshot.pipeline_qa.oge_catalog_source as loader


EVIDENCE, REVIEW = 'a'*40, 'b'*40


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


class OgeCatalogSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-catalog-source-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls = []
        self.objects = {}
        self.archive([row(DIRECT), row()])
        self.report = archive_direct_pdf(self.root, self.catalog['transactions'][0],
            OgeSourceConfig(True, True), client=Client(), retrieved_at='2026-10-27T00:00:00Z')
        self.collect()

    def archive(self, rows):
        pages, raw_pages = [], []
        for index, item in enumerate(rows):
            raw = encoded(payload([item], total=len(rows)))
            pages.append(parse_catalog_page(json.loads(raw), start=index, length=1))
            raw_pages.append((index, 1, raw, {'content-type': 'application/json'}))
        self.catalog = build_catalog(pages)
        self.meta = archive_catalog(self.root, raw_pages, self.catalog, retrieved_at='2026-10-27T00:00:00Z')
        self.collect()
        self.objects[(REVIEW, 'status/oge.json')] = encoded({'catalog_sha256': self.meta['sha256']})

    def collect(self):
        for path in self.root.rglob('*'):
            if path.is_file():
                self.objects[(EVIDENCE, path.relative_to(self.root).as_posix())] = path.read_bytes()

    def read(self, repo, ref, path):
        self.calls.append((ref, path))
        return self.objects[(ref, path)]

    def sources(self):
        return _Sources(self.root, self.read)

    def paths(self, repo, ref, folder):
        prefix = folder + '/'
        return sorted(path[len(prefix):] for (selected, path) in self.objects
                      if selected == ref and path.startswith(prefix))

    def load(self, *, selected_sha=None):
        sources = self.sources()
        catalog, archive = loader.load_fixed_oge_catalog(sources, EVIDENCE, REVIEW)
        record = self.catalog['transactions'][0]
        bound = loader.load_fixed_oge_direct_source(sources, EVIDENCE, catalog=catalog,
            document_id=record['source_document_id'], source_url=record['document_url'],
            source_sha256=selected_sha, list_paths=self.paths)
        return catalog, archive, bound

    def replace_metadata(self, changes):
        path = self.report['archive_path'][:-4] + '.json'
        metadata = json.loads(self.objects[(EVIDENCE, path)])
        metadata.update(changes)
        self.objects[(EVIDENCE, path)] = encoded(metadata)

    def test_complete_two_page_original_replay_binds_direct_metadata_and_pdf(self):
        before = deepcopy(self.objects)
        catalog, metadata, (record, source_meta, raw) = self.load(selected_sha=self.report['sha256'])
        self.assertEqual(catalog, self.catalog)
        self.assertEqual(metadata, self.meta)
        self.assertEqual(record, self.catalog['transactions'][0])
        self.assertEqual(source_meta, self.report)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.report['sha256'])
        self.assertTrue(all((EVIDENCE, p['archive_path']) in self.calls for p in self.meta['pages']))
        self.assertEqual(self.objects, before)

    def test_self_signed_normalized_and_repointed_metadata_cannot_replace_raw_catalog_identity(self):
        forged = deepcopy(self.catalog); forged['transactions'][0]['filer_name'] = 'Forged, Identity'
        raw = encoded(forged); digest = hashlib.sha256(raw).hexdigest()
        meta = deepcopy(self.meta)
        meta.update(sha256=digest, archive_path=f'oge/catalog/{digest}.json',
                    normalized_archive_path=f'oge/catalog/normalized/{digest}.json')
        self.objects[(EVIDENCE, meta['archive_path'])] = encoded(meta)
        self.objects[(EVIDENCE, meta['normalized_archive_path'])] = raw
        self.objects[(REVIEW, 'status/oge.json')] = encoded({'catalog_sha256': digest})
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'complete original-page replay'):
            self.load()
        self.assertFalse(any(path.endswith('.pdf') for _, path in self.calls))

    def test_no_latest_fallback_when_fixed_review_catalog_pointer_is_missing_or_wrong(self):
        for status in ({}, {'catalog_sha256': 'main'}, {'catalog_sha256': 'c'*64}):
            with self.subTest(status=status):
                self.objects[(REVIEW, 'status/oge.json')] = encoded(status)
                with self.assertRaises((loader.OgeCatalogSourceError, KeyError)):
                    self.load()

    def test_every_original_page_hash_length_and_complete_pagination_are_required(self):
        original = deepcopy(self.objects)
        second = self.meta['pages'][1]['archive_path']
        for kind in ('missing', 'hash', 'length', 'gap'):
            with self.subTest(kind=kind):
                self.objects = deepcopy(original)
                meta = deepcopy(self.meta)
                if kind == 'missing': del self.objects[(EVIDENCE, second)]
                elif kind == 'hash': self.objects[(EVIDENCE, second)] += b' '
                elif kind == 'length': meta['pages'][1]['byte_length'] += 1
                else: meta['pages'][1]['start'] = 2
                self.objects[(EVIDENCE, meta['archive_path'])] = encoded(meta)
                with self.assertRaises((loader.OgeCatalogSourceError, KeyError)):
                    self.load()

    def test_catalog_counts_source_URL_and_paths_do_not_inherit_trust_from_hash(self):
        original = deepcopy(self.objects)
        for changes in ({'direct_pdf_count': 2}, {'record_count': True}, {'page_count': True},
                        {'source_url': 'https://example.invalid/'},
                        {'normalized_archive_path': 'other/catalog.json'}, {'source_id': 'other'}):
            with self.subTest(changes=changes):
                self.objects = deepcopy(original)
                self.objects[(EVIDENCE, self.meta['archive_path'])] = encoded({**self.meta, **changes})
                with self.assertRaises(loader.OgeCatalogSourceError): self.load()

    def test_unique_evidence_version_is_not_selected_by_audit_SHA_among_multiple_versions(self):
        folder = str(Path(self.report['archive_path']).parent).replace('\\', '/')
        for extension in ('.pdf', '.json'):
            self.objects[(EVIDENCE, folder + '/' + 'd'*64 + extension)] = b'not read'
        self.calls.clear()
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'multiple evidence SHA'):
            self.load(selected_sha=self.report['sha256'])
        self.assertFalse(any('/oge/reports/' in '/'+path for _, path in self.calls))

    def test_wrong_selected_SHA_URL_or_document_does_not_fetch_a_different_source(self):
        sources = self.sources()
        catalog, _ = loader.load_fixed_oge_catalog(sources, EVIDENCE, REVIEW)
        record = catalog['transactions'][0]
        arguments = dict(catalog=catalog, document_id=record['source_document_id'],
                         source_url=record['document_url'], list_paths=self.paths)
        for changes in ({'source_sha256': 'd'*64}, {'document_id': 'd'*32},
                        {'source_url': record['document_url'].replace('Example-278T', 'Other-278T')}):
            with self.subTest(changes=changes), self.assertRaises(loader.OgeCatalogSourceError):
                loader.load_fixed_oge_direct_source(sources, EVIDENCE, **{**arguments, **changes})

    def test_direct_source_identity_bytes_and_optional_field_types_are_exact(self):
        original = deepcopy(self.objects)
        for changes in ({'document_id': 'd'*32}, {'filer_name': 'Other, Person'},
                        {'agency': 'Other'}, {'position_title': 'Other'},
                        {'pending_final_oge_disposition': 0}, {'byte_length': self.report['byte_length']+1},
                        {'archive_path': '../source.pdf'}):
            with self.subTest(changes=changes):
                self.objects = deepcopy(original); self.replace_metadata(changes)
                with self.assertRaises(loader.OgeCatalogSourceError): self.load()
        self.objects = deepcopy(original)
        self.objects[(EVIDENCE, self.report['archive_path'])] += b'changed'
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'PDF bytes differ'):
            self.load()

    def test_duplicate_exact_catalog_occurrences_are_collapsed_but_identity_conflicts_reject(self):
        self.archive([row(DIRECT), row(DIRECT)])
        catalog, _, _ = self.load()
        self.assertEqual(len(catalog['transactions']), 2)
        changed = row(DIRECT); changed['name'] = 'Other, Person'
        self.archive([row(DIRECT), changed])
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'catalog identity is invalid'):
            self.load()

    def test_budgets_paths_and_missing_pair_fail_before_pdf_read(self):
        with patch.object(loader, 'MAX_CATALOG_ROWS', 1):
            with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'row inventory'):
                self.load()
        with patch.object(loader, 'MAX_SOURCE_PATHS', 1):
            with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'path inventory'):
                self.load()
        self.objects[(EVIDENCE, str(Path(self.report['archive_path']).parent).replace('\\','/') + '/../../escape.pdf')] = b'x'
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'path inventory'):
            self.load()
        del self.objects[(EVIDENCE, str(Path(self.report['archive_path']).parent).replace('\\','/') + '/../../escape.pdf')]
        del self.objects[(EVIDENCE, self.report['archive_path'])]
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'pair is incomplete'):
            self.load()

    def test_fixed_refs_and_duplicate_JSON_keys_are_not_self_signed_selections(self):
        for evidence, review in ((None, REVIEW), (EVIDENCE, 'review')):
            with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'fixed evidence and review'):
                loader.load_fixed_oge_catalog(self.sources(), evidence, review)
        self.objects[(REVIEW, 'status/oge.json')] = (b'{"catalog_sha256":"'+self.meta['sha256'].encode()+
            b'","catalog_sha256":"'+self.meta['sha256'].encode()+b'"}')
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'repeats a key'):
            self.load()

    def test_actual_fixed_Git_default_path_inventory_and_blob_reads(self):
        for (ref, path), raw in self.objects.items():
            if ref == REVIEW:
                target = self.root/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        for args in (('init', '-q'), ('config', 'user.name', 'Source Contract'),
                     ('config', 'user.email', 'source@example.invalid'), ('add', '.'), ('commit', '-qm', 'fixed source')):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        commit = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        sources = _Sources(self.root, git_object)
        catalog, _ = loader.load_fixed_oge_catalog(sources, commit, commit)
        record = catalog['transactions'][0]
        _, meta, raw = loader.load_fixed_oge_direct_source(sources, commit, catalog=catalog,
            document_id=record['source_document_id'], source_url=record['document_url'])
        self.assertEqual(meta, self.report)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.report['sha256'])
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.root), 'status', '--porcelain']), b'')
        limited = _Sources(self.root, git_object)
        with patch.object(limited, 'read_object', wraps=git_object) as blob_reader, \
                patch.object(loader, 'MAX_JSON_BYTES', 4):
            with self.assertRaisesRegex(ValueError, 'missing or exceeds byte budget'):
                loader.load_fixed_oge_catalog(limited, commit, commit)
            blob_reader.assert_not_called()  # cat-file size rejects before allocating/decoding the blob.

    def test_workflow_review_status_and_real_archive_shape_need_no_new_manifest(self):
        self.objects[(REVIEW, 'status/oge.json')] = encoded({
            'schema_version': 'oge-review-run/v1', 'source_id': 'oge', 'status': 'partial',
            'generated_at': '2026-10-27T00:00:00Z', 'catalog_sha256': self.meta['sha256'],
            'catalog_record_count': 2, 'direct_pdf_count': 1, 'request_required_count': 1,
            'retained_historical_direct_count': 0})
        self.assertEqual(self.load()[2][1], self.report)
        self.objects[(REVIEW, 'status/oge.json')] = encoded({
            'schema_version': 'oge-source-run/v1', 'source_id': 'oge', 'catalog': self.meta})
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'selection is unavailable'):
            self.load()

    def test_mixed_case_nested_and_aliased_paths_never_claim_the_bound_original(self):
        catalog, _ = loader.load_fixed_oge_catalog(self.sources(), EVIDENCE, REVIEW)
        record = catalog['transactions'][0]; sha = self.report['sha256']
        ordinary = [sha+'.json', sha+'.pdf']
        for extra in (sha.upper()+'.PDF', './'+sha+'.pdf', 'sub/'+sha+'.pdf',
                      sha+'.PDF', '..\\'+sha+'.pdf', sha+'.pdf'):
            with self.subTest(extra=extra):
                self.calls.clear()
                with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'path inventory'):
                    loader.load_fixed_oge_direct_source(self.sources(), EVIDENCE, catalog=catalog,
                        document_id=record['source_document_id'], source_url=record['document_url'],
                        list_paths=lambda *args: ordinary+[extra])
                self.assertEqual(self.calls, [])

    def test_identical_raw_page_SHA_can_be_two_real_occurrences_but_repeated_start_is_not_complete(self):
        self.archive([row(DIRECT), row(DIRECT)])
        self.assertEqual(self.meta['pages'][0]['sha256'], self.meta['pages'][1]['sha256'])
        self.assertEqual(len(self.load()[0]['transactions']), 2)
        changed = deepcopy(self.meta); changed['pages'][1]['start'] = 0
        self.objects[(EVIDENCE, changed['archive_path'])] = encoded(changed)
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'gap or overlap'):
            self.load()

    def test_raw_record_limit_precedes_row_parser_and_cumulative_work_is_bounded(self):
        meta = deepcopy(self.meta)
        too_many = payload([row(DIRECT)], total=loader.MAX_CATALOG_ROWS+1)
        raw = encoded(too_many); sha = hashlib.sha256(raw).hexdigest()
        meta['pages'][0].update(sha256=sha, byte_length=len(raw), archive_path=f'oge/catalog/pages/{sha}.json')
        self.objects[(EVIDENCE, meta['pages'][0]['archive_path'])] = raw
        self.objects[(EVIDENCE, meta['archive_path'])] = encoded(meta)
        with patch.object(loader, 'parse_catalog_page', wraps=loader.parse_catalog_page) as parser:
            with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'row inventory'):
                self.load()
            parser.assert_not_called()
        whole = encoded(payload([row(DIRECT), row()]))
        sha = hashlib.sha256(whole).hexdigest()
        self.objects[(EVIDENCE, f'oge/catalog/pages/{sha}.json')] = whole
        meta['pages'] = [{'start': start, 'length': 2, 'sha256': sha, 'byte_length': len(whole),
                          'archive_path': f'oge/catalog/pages/{sha}.json'} for start in (0, 2)]
        self.objects[(EVIDENCE, meta['archive_path'])] = encoded(meta)
        with patch.object(loader, 'MAX_CATALOG_ROWS', 3), patch.object(loader, 'parse_catalog_page',
                wraps=loader.parse_catalog_page) as parser:
            with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'cumulative source rows'):
                self.load()
            self.assertEqual(parser.call_count, 1)

    def test_default_Git_path_count_is_bounded_before_splitlines_materialization(self):
        folder = self.root/Path(self.report['archive_path']).parent
        for index in range(loader.MAX_SOURCE_PATHS):
            (folder/(f'{index:064x}'+'.json')).write_bytes(b'not fetched')
        for args in (('init', '-q'), ('config', 'user.name', 'Source Contract'),
                     ('config', 'user.email', 'source@example.invalid'), ('add', '.'), ('commit', '-qm', 'fixed path inventory')):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        commit = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'path inventory exceeds budget'):
            loader._git_paths(self.root, commit, folder.relative_to(self.root).as_posix())

    def add_discovery(self, catalog=None, metadata=None):
        catalog = self.catalog if catalog is None else catalog
        metadata = self.meta if metadata is None else metadata
        path = f'oge/discoveries/{metadata["sha256"]}.json'
        self.objects[(REVIEW, path)] = encoded({**catalog, 'metadata': metadata})
        return path

    def catalogs(self):
        return loader.load_fixed_oge_catalogs(self.sources(), EVIDENCE, REVIEW, list_paths=self.paths)

    def test_full_verified_history_retains_removed_document_without_falsifying_current_counts(self):
        old_catalog, old_meta = deepcopy(self.catalog), deepcopy(self.meta)
        old_record = old_catalog['transactions'][0]
        self.add_discovery(old_catalog, old_meta)
        self.archive([row()])  # Actual new raw catalog has no direct document.
        self.add_discovery()  # Normal workflow also saves the current discovery.
        current, history = self.catalogs()
        self.assertEqual(current, self.catalog)
        self.assertEqual(current['records_total'], 1)
        self.assertFalse(any(r['access_method'] == 'direct_pdf' for r in current['transactions']))
        self.assertEqual(history, [{**old_catalog, 'metadata': old_meta}])
        record, meta, _ = loader.load_fixed_oge_direct_source(self.sources(), EVIDENCE,
            catalog=current, catalog_history=history, document_id=old_record['source_document_id'],
            source_url=old_record['document_url'], list_paths=self.paths)
        self.assertEqual(record, old_record)
        self.assertEqual(meta, self.report)
        self.assertEqual(self.catalogs(), (current, history))

    def test_history_wrapper_filename_metadata_and_entire_body_must_match_original(self):
        path = self.add_discovery()
        original = deepcopy(self.objects)
        for kind in ('no_metadata', 'filename', 'metadata', 'body', 'extra', 'typed_count'):
            with self.subTest(kind=kind):
                self.objects = deepcopy(original)
                wrapper = json.loads(self.objects[(REVIEW, path)])
                if kind == 'no_metadata': wrapper.pop('metadata')
                elif kind == 'filename': wrapper['metadata']['sha256'] = 'e'*64
                elif kind == 'metadata': wrapper['metadata']['retrieved_at'] = 'other observation'
                elif kind == 'body': wrapper['transactions'][0]['filer_name'] = 'Forged, Person'
                elif kind == 'extra': wrapper['passed'] = True
                else: wrapper['catalog_rows_covered'] = float(wrapper['catalog_rows_covered'])
                self.objects[(REVIEW, path)] = encoded(wrapper)
                with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'discovery'):
                    self.catalogs()

    def test_complete_history_set_does_not_stop_after_a_valid_target_or_ignore_missing_evidence(self):
        target_path = self.add_discovery()
        bad_path = 'oge/discoveries/'+'f'*64+'.json'
        self.objects[(REVIEW, bad_path)] = encoded({'metadata': {'sha256': 'f'*64}})
        self.calls.clear()
        with self.assertRaises(KeyError):
            self.catalogs()
        self.assertIn((REVIEW, target_path), self.calls)
        self.assertIn((REVIEW, bad_path), self.calls)
        self.assertIn((EVIDENCE, 'oge/catalog/'+'f'*64+'.json'), self.calls)
        del self.objects[(REVIEW, bad_path)]
        # A historical discovery declares a source dependency; normalized JSON
        # on its own cannot replace a missing original page.
        del self.objects[(EVIDENCE, self.meta['pages'][1]['archive_path'])]
        with self.assertRaises(KeyError):
            self.catalogs()

    def test_history_path_inventory_is_complete_validated_and_bounded_before_wrapper_reads(self):
        self.add_discovery()
        name = self.meta['sha256']+'.json'
        for names in ([name, name], [name.upper()], ['./'+name], ['nested/'+name],
                      ['..\\'+name], [name]*129):
            with self.subTest(names=names[:2]):
                self.calls.clear()
                with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'discovery path inventory'):
                    loader.load_fixed_oge_catalogs(self.sources(), EVIDENCE, REVIEW,
                                                  list_paths=lambda *args: names)
                self.assertFalse(any('/discoveries/' in path for _, path in self.calls))
        with patch.object(loader, 'MAX_CATALOG_HISTORY', 0):
            with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'discovery path inventory'):
                self.catalogs()

    def test_all_history_identity_conflicts_reject_and_exact_duplicates_use_normal_collapse(self):
        current_sha = self.meta['sha256']
        self.archive([row(DIRECT)])
        historical_path = self.add_discovery()
        self.objects[(REVIEW, 'status/oge.json')] = encoded({'catalog_sha256': current_sha})
        current, history = self.catalogs()
        self.assertEqual(len(current['transactions']), 2)
        self.assertEqual(len(history), 1)
        with patch.object(loader, 'MAX_DIRECT_OCCURRENCES', 1):
            with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'direct occurrences exceed budget'):
                self.catalogs()
        changed = row(DIRECT); changed['agency'] = 'Conflicting Agency'
        self.archive([changed]); self.add_discovery()
        self.objects[(REVIEW, 'status/oge.json')] = encoded({'catalog_sha256': current_sha})
        self.assertIn((REVIEW, historical_path), self.objects)
        with self.assertRaisesRegex(loader.OgeCatalogSourceError, 'catalog identity is invalid'):
            self.catalogs()

    def test_actual_fixed_Git_missing_history_is_empty_and_declared_history_is_replayed(self):
        old_catalog, old_meta = deepcopy(self.catalog), deepcopy(self.meta)
        old_record = old_catalog['transactions'][0]
        for args in (('init', '-q'), ('config', 'user.name', 'History Contract'),
                     ('config', 'user.email', 'history@example.invalid')):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        def commit():
            for (ref, path), raw in self.objects.items():
                if ref == REVIEW:
                    target = self.root/path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'fixed historical catalog'],
                           check=True, capture_output=True)
            return subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        first = commit()
        sources = _Sources(self.root, git_object)
        self.assertEqual(loader.load_fixed_oge_catalogs(sources, first, first), (old_catalog, []))
        self.add_discovery(old_catalog, old_meta)
        self.archive([row()]); self.add_discovery()
        second = commit()
        sources = _Sources(self.root, git_object)
        current, history = loader.load_fixed_oge_catalogs(sources, second, second)
        self.assertEqual(current, self.catalog)
        self.assertEqual(history, [{**old_catalog, 'metadata': old_meta}])
        record, metadata, raw = loader.load_fixed_oge_direct_source(sources, second,
            catalog=current, catalog_history=history, document_id=old_record['source_document_id'],
            source_url=old_record['document_url'])
        self.assertEqual((record, metadata), (old_record, self.report))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), metadata['sha256'])
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.root), 'status', '--porcelain']), b'')


if __name__ == '__main__':
    unittest.main()
