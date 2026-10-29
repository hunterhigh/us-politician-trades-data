"""Existing printed-number quad method through the normal saved-input route.

Generated source pixels and construction-controlled saved words/TSV exercise
source replay and qualification, not OCR accuracy. No recognizer is invoked.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from test_oge_candidate import base
from test_oge_source_quad_ingestion import actual_source_quad_fixture, EDGES, write
from unison_snapshot.ocr_geometry import OcrPage
from unison_snapshot.oge_278t_raster_slots import DUAL_MODE, observe_raster_slots
from unison_snapshot.oge_278t_source_quad_cells import (
    observe_saved_source_quad_cells, source_quad_cell,
)
from unison_snapshot.oge_saved_observations import prepare_saved_oge_observations
from unison_snapshot.oge_transaction_adapter import materialize_oge_278t_observations
from unison_snapshot.pdf_text_objects import render_pdf_text_objects


ROOT = Path(__file__).resolve().parents[2]
CUTOFF = '2026-06-20T00:00:00Z'


def printed_number_reading(data, primary, rendered, *, tokens=('1',)):
    """Map the known construction number box through the actual source warp."""
    page = primary['pages'][0]
    slot = next(row for row in page['slots'] if row['words'])
    recipe, _ = source_quad_cell(rendered, page, slot, 'printed_number', layer='full_source')
    word, = [word for word in data['truth_words'] if word['top'] > 116
             and EDGES[0] < word['x0'] < EDGES[1]]
    affine = rendered['receipt']['pixel_edge_to_source_pdf_affine']
    media = rendered['receipt']['source_mediabox_pdf_bottom_left']
    inverse = np.linalg.inv(np.array([[affine[0], affine[2], affine[4]],
                                     [affine[1], affine[3], affine[5]], [0., 0., 1.]]))
    warp = np.array(recipe['source_pixel_to_cell_matrix'])
    points = []
    for x, y in ((word['x0'], word['top']), (word['x1'], word['top']),
                 (word['x1'], word['bottom']), (word['x0'], word['bottom'])):
        point = inverse @ np.array([x + media[0], media[3] - y, 1.])
        point = warp @ np.array([point[0] / point[2], point[1] / point[2], 1.])
        points.append((point[0] / point[2] + recipe['padding'],
                       point[1] / point[2] + recipe['padding']))
    left = math.floor(min(point[0] for point in points))
    top = math.floor(min(point[1] for point in points))
    right = math.ceil(max(point[0] for point in points))
    bottom = math.ceil(max(point[1] for point in points))
    lines = ['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext']
    for index, token in enumerate(tokens, 1):
        lines.append(f'5\t1\t1\t1\t1\t{index}\t{left}\t{top}\t{right-left}\t{bottom-top}\t95\t{token}')
    text = '\n'.join(lines) + '\n'
    recipe.update(physical_index=slot['slot_index'] - 1, column=0,
                  tsv_sha256=hashlib.sha256(text.encode()).hexdigest())
    saved = [{'page_number': 2, 'render_receipt': rendered['receipt'],
              'selected_slot_indices': [slot['slot_index']],
              'cells': [{'slot_index': slot['slot_index'], 'recipe': recipe, 'tsv_utf8': text}]}]
    # Reuse only the actual render already obtained for this exact source/page.
    # Normal writer/consumer replay below opens the original independently.
    cache = {(str(data['source'].resolve()), data['sha'], 2, 300): rendered}
    return observe_saved_source_quad_cells(data['source'], slots_observation=primary,
        field='printed_number', saved_pages=saved, layer='full_source',
        ocr_engine='construction-controlled saved number TSV; no OCR', render_cache=cache)


class SavedPrintedNumberIngestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='oge-printed-source-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.data = actual_source_quad_fixture(cls.root)

        def primary(unknown):
            words = [{**word, 'text': '?' if unknown and word['text'] == '1' else word['text'],
                      'ocr_confidence': 95., 'block_num': 1, 'par_num': 1, 'line_num': index}
                     for index, word in enumerate(cls.data['truth_words'], 1)]
            return observe_raster_slots(cls.data['source'], expected_sha256=cls.data['sha'],
                page_numbers=[2], resolution=300, geometry_mode=DUAL_MODE,
                saved_ocr_pages={2: OcrPage(width=792, height=612, words=words)},
                saved_ocr_engine='construction-controlled saved words; no OCR')

        cls.baseline = primary(False)
        cls.unknown = primary(True)
        rendered = render_pdf_text_objects(cls.data['source'], expected_sha256=cls.data['sha'],
                                           page_number=2, resolution=300)
        cls.number = printed_number_reading(cls.data, cls.unknown, rendered)
        cls.bad_numbers = [printed_number_reading(cls.data, cls.unknown, rendered, tokens=tokens)
                           for tokens in (('0',), ('1', '1'))]

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='oge-printed-consumer-')
        self.addCleanup(temporary.cleanup)
        self.case = Path(temporary.name)
        self.review = self.case / 'review'
        shutil.copytree(self.data['review'] / 'oge/extractions', self.review / 'oge/extractions')
        write(self.case / 'catalog.json', self.data['catalog'])
        write(self.case / 'base.json', base())
        self.primary_path, self.secondary_path = self.case / 'primary.json', self.case / 'number.json'
        self.ledger = self.case / 'fields.json'
        self.package = (self.review / 'oge/saved_observations' / self.data['document'] /
                        self.data['sha'] / 'saved-observations.json')
        self.output, self.audit = self.case / 'candidate.json', self.case / 'audit.json'

    def command(self, *arguments):
        return subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT,
            env={**os.environ, 'PYTHONPATH': str(ROOT / 'backend/src'), 'PYTHONIOENCODING': 'utf-8'},
            capture_output=True, text=True)

    def consume(self, primary, secondary=None):
        write(self.primary_path, primary)
        arguments = [ROOT / 'backend/src/unison_snapshot/commands/observe_oge_278t_fields.py',
            '--observations', self.primary_path, '--review-root', self.review,
            '--evidence-root', self.data['evidence'], '--metadata', self.data['metadata_path'],
            '--output', self.ledger]
        if secondary is not None:
            write(self.secondary_path, secondary)
            arguments += ['--secondary-observations', self.secondary_path]
        writer = self.command(*arguments)
        self.assertEqual(writer.returncode, 0, writer.stderr)
        candidate = self.command('-m', 'unison_snapshot', 'build-oge-candidate',
            '--catalog', self.case / 'catalog.json', '--extractions-dir', self.review / 'oge/extractions',
            '--evidence-root', self.data['evidence'], '--saved-observations', self.package,
            '--base', self.case / 'base.json', '--data-cutoff-at', CUTOFF,
            '--output', self.output, '--audit-output', self.audit)
        self.assertEqual(candidate.returncode, 0, candidate.stderr)
        return json.loads(self.output.read_bytes()), json.loads(self.audit.read_bytes())

    def test_unknown_primary_number_restores_exact_baseline_fact_through_writer_and_candidate_cli(self):
        self.assertEqual(self.data['old']['transactions'], [])
        source_bytes = self.data['source'].read_bytes()
        baseline, baseline_audit = self.consume(self.baseline)
        candidate, audit = self.consume(self.unknown, self.number)
        self.assertEqual(len(candidate['transactions']), 1)
        self.assertEqual(candidate['transactions'], baseline['transactions'])
        fact = candidate['transactions'][0]
        self.assertEqual((fact['asset_name'], fact['transaction_type'], fact['owner'],
                          fact['amount_low'], fact['amount_high'], fact['transaction_date']),
                         ('ALPHA Fund', 'purchase', 'Undisclosed', 50001, 100000, '2026-06-01'))
        self.assertEqual(fact['source_url'], self.data['record']['document_url'])
        receipt = audit['reports'][0]['observation_receipt']
        self.assertFalse(receipt['coverage_complete'])
        self.assertEqual(receipt['observed_slot_count'], 31)
        self.assertEqual(receipt['unresolved_slot_count'], 30)
        self.assertEqual(receipt['unresolved_slot_count'],
                         baseline_audit['reports'][0]['observation_receipt']['unresolved_slot_count'])
        ledger = json.loads(self.ledger.read_bytes())
        reads = ledger['pages'][0]['slots'][0]['field_readings']['printed_number']['observations']
        self.assertIsNone(reads[0]['shape']['value'])
        self.assertEqual(reads[1]['shape']['value'], 1)
        self.assertEqual(reads[1]['geometry_status'], 'source_bound')
        self.assertEqual(self.data['source'].read_bytes(), source_bytes)

    def test_zero_or_repeated_number_read_never_qualifies_the_affected_row(self):
        for reading in self.bad_numbers:
            with self.subTest(text=reading['pages'][0]['slots'][0]['column_raw_ocr_text']):
                value = materialize_oge_278t_observations(self.data['old'], self.data['record'],
                    observations=[self.unknown], document_page_count=2,
                    secondary_observations=[reading], archived_pdf_path=str(self.data['source']))
                self.assertEqual(value['transactions'], [])
                self.assertFalse(value['observation_receipt']['coverage_complete'])
                self.assertTrue(any('printed_number_reading_unresolved' in row.get('reasons', [])
                                    for row in value['observation_receipt']['rows']))

    def test_date_content_crop_wrong_source_and_annotations_remain_rejected_before_replay(self):
        variants = []
        date = deepcopy(self.number)
        date['schema_version'] = 'oge-278t-date-column-observation/v1'
        date['source_quad_input']['field'] = 'transaction_date'
        variants.append(date)
        content = deepcopy(self.number)
        content['source_quad_input']['content_crop_mode'] = 'source_quad_isolated_number_ink/v1'
        variants.append(content)
        wrong = deepcopy(self.number)
        wrong['source_sha256'] = '0' * 64
        variants.append(wrong)
        annotated = deepcopy(self.number)
        annotated['source_quad_input']['saved_pages'][0]['render_receipt'][
            'annotation_count_excluded_from_all_renders'] = 1
        variants.append(annotated)
        with patch('unison_snapshot.oge_278t_source_quad_cells.validate_source_quad_reading',
                   side_effect=AssertionError('unsupported input must fail before source replay')):
            for reading in variants:
                with self.subTest(inputs=reading['source_quad_input']):
                    with self.assertRaises(ValueError):
                        prepare_saved_oge_observations(evidence_root=self.data['evidence'],
                            metadata_path=self.data['metadata_path'], observations=[self.unknown],
                            secondary_observations=[reading])


if __name__ == '__main__':
    unittest.main()
