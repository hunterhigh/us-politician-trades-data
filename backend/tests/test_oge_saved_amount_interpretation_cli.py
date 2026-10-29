"""Actual saved writer/normal CLI contracts, with generated source/readings.

These tests certify transport, isolation and preservation, not OCR accuracy or
successful reinterpretation of the historical 44-page official package.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_oge_content_relations import saved
from test_oge_cover_consumption import CUTOFF, DOCUMENT_ID, write
from test_oge_saved_observations_cli import ROOT, normal_fixture, run_cli
from test_public_repo import FakeTransport
from unison_snapshot.builder import build
from unison_snapshot.disclosure_status import attach_disclosure_status
from unison_snapshot.oge_278t_field_shadow import (
    LEGACY_AMOUNT_INTERPRETATION_MODE as MODE, _amount_shape,
)
from unison_snapshot.oge_saved_observations import load_saved_oge_observations
from unison_snapshot.public_repo import PublicSnapshotRepository


def secondary_amounts(observation, affected_index):
    slots = []
    for source in observation['pages'][0]['slots']:
        columns = source['column_observation']
        primary = ' '.join(columns[name]['raw_ocr_text'] for name in ('notification', 'amount'))
        raw = 'Yos $50,001-$100,000' if source['slot_index'] == affected_index else primary.strip()
        top = source['band_pdf_points'][0]
        shape = ({'status':'ocr_shape_only', 'values':[50001,100000], 'reason':None}
                 if source['slot_index'] == affected_index else _amount_shape(raw))
        slots.append({'slot_index':source['slot_index'], 'band_pdf_points':source['band_pdf_points'],
            'page_raw_ocr_text':primary, 'column_raw_ocr_text':raw, 'column_shape':shape,
            'column_detections':([{'text':raw, 'bbox_pdf_points':[111,top+2,148,top+8]}] if raw else [])})
    return {'schema_version':'oge-278t-amount-column-observation/v2',
        'source_sha256':observation['source_sha256'], 'ocr_engine':'synthetic-column',
        'ocr_resolution_dpi':300, 'input_ocr_resolution_dpi':200,
        'summary':{'observed_slots':len(slots)}, 'pages':[{'page_number':2,
            'status':'amount_column_observed', 'slot_count':len(slots), 'slots':slots}]}




if __name__ == '__main__':
    unittest.main()
