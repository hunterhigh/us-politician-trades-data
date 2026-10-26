"""Project saved House asset words through existing OCR-line geometry checks.

The original raw text is retained. This can repair word ordering, never choose
between different characters, invent text, or give OCR labels fact authority.
"""
from copy import deepcopy
import io
import math

from .oge_ocr_line_order import (MODE, prepare_line_source, validate_line_source,
                                  description_line_reading)

SCHEMA = 'house-asset-line-source/v1'
REFS = 'asset_line_word_indices'


def validate_asset_line_mode(observation):
    mode = observation.get('asset_line_mode')
    if mode not in (None, MODE):
        raise ValueError('House asset OCR line mode is unsupported')
    if mode is None and any('asset_line_source' in page or any(
            REFS in (row.get('cells') or {}).get('asset', {}) for row in page.get('rows', []))
            for page in observation.get('pages', [])):
        raise ValueError('House asset line source requires its explicit mode')


def _member(word, box):
    return (box[0] <= (word['x0'] + word['x1']) / 2 <= box[2] and
            box[1] <= (word['top'] + word['bottom']) / 2 <= box[3])


def _legacy_text(words):
    lines = []
    for word in sorted(words, key=lambda word: (word['top'], word['x0'])):
        if lines and word['top'] - min(item['top'] for item in lines[-1]) <= 5:
            lines[-1].append(word)
        else:
            lines.append([word])
    return ' '.join(word['text'] for line in lines for word in sorted(line, key=lambda word: word['x0']))


def attach_asset_line_source(page, words, *, width, height, source_sha256):
    """Retain complete existing page words without editing any first reading."""
    if page.get('coordinate_frame') != 'original_page':
        return
    source = prepare_line_source(words, source_sha256=source_sha256, page_number=page['page'])
    if source is None:
        return
    page['asset_line_source'] = {'schema_version': SCHEMA, 'page_size_pdf_points': [width, height], 'source': source}
    for row in page['rows']:
        asset = (row.get('cells') or {}).get('asset')
        if asset is None:
            continue
        x0, y0, x1, y1 = asset['bbox_normalized']
        box = [x0 * width, y0 * height, x1 * width, y1 * height]
        asset[REFS] = [index for index, word in enumerate(words) if _member(word, box)]


def validate_asset_line_pdf(pdf_bytes, observation):
    """Rebind saved page dimensions to the already hash-checked original PDF."""
    validate_asset_line_mode(observation)
    if observation.get('asset_line_mode') != MODE:
        return
    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in observation['pages']:
            proof = page.get('asset_line_source')
            if proof is None:
                continue
            number = page.get('page')
            if type(number) is not int or not 1 <= number <= len(pdf.pages):
                raise ValueError('House asset source page is invalid')
            original = pdf.pages[number - 1]
            if (page.get('coordinate_frame') != 'original_page' or tuple(original.bbox[:2]) != (0, 0) or
                    tuple(original.cropbox or original.mediabox) != tuple(original.mediabox) or
                    proof.get('page_size_pdf_points') != [original.width, original.height]):
                raise ValueError('House asset line source dimensions differ from original PDF')
            image = original.to_image(resolution=observation['render_dpi']).original
            for row in page.get('rows', []):
                cell = (row.get('cells') or {}).get('asset')
                if cell is None:
                    continue
                box = cell.get('bbox_normalized')
                if (not isinstance(box, list) or len(box) != 4 or
                        any(type(v) not in (int, float) or not math.isfinite(v) for v in box) or
                        not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1):
                    raise ValueError('House asset line source cell geometry is invalid')
                expected_pixels = [int(box[0]*image.width), int(box[1]*image.height),
                                   int(box[2]*image.width), int(box[3]*image.height)]
                expected_points = [round(box[0]*original.width, 2), round(box[1]*original.height, 2),
                                   round(box[2]*original.width, 2), round(box[3]*original.height, 2)]
                if (cell.get('bbox_pixels') != expected_pixels or cell.get('bbox_points') != expected_points or
                        row.get('band') != [box[1], box[3]]):
                    raise ValueError('House asset line source crop frames or physical band differ')


def project_asset_line_reading(observation, page_number, physical):
    """Return a unique geometric order; absent/ambiguous sources supply no fix."""
    validate_asset_line_mode(observation)
    if observation.get('asset_line_mode') != MODE:
        return None
    pages = [page for page in observation['pages'] if page.get('page') == page_number]
    if len(pages) != 1:
        raise ValueError('House asset line page is not unique')
    page = pages[0]; proof = page.get('asset_line_source')
    cell = (physical.get('cells') or {}).get('asset')
    if proof is None:
        if cell is not None and REFS in cell:
            raise ValueError('House asset line references lack their source')
        return None
    if not isinstance(proof, dict) or proof.get('schema_version') != SCHEMA:
        raise ValueError('House asset line source version differs')
    width, height = proof['page_size_pdf_points']
    source = proof['source']; words = source['words']
    if (any(not 0 <= word['x0'] < word['x1'] <= width or
            not 0 <= word['top'] < word['bottom'] <= height or
            not 0 <= word['ocr_confidence'] <= 100 for word in words)):
        raise ValueError('House asset line source words exceed their page')
    if cell is None:
        return None
    x0, top, x1, bottom = cell['bbox_normalized']
    box = [x0 * width, top * height, x1 * width, bottom * height]
    selected = [index for index, word in enumerate(words) if _member(word, box)]
    if cell.get(REFS) != selected:
        raise ValueError('House asset line word membership differs from source')
    if _legacy_text([words[index] for index in selected]) != cell['raw_text']:
        raise ValueError('House asset original raw reading differs from source words')
    # Keep whole-row memberships for the shared OGE cross-row guard, and only
    # the asset column indices for its actual description projection.
    row_indices = sorted([index for index, word in enumerate(words)
        if box[1] <= (word['top'] + word['bottom']) / 2 < box[3]],
        key=lambda index: (words[index]['top'], words[index]['x0']))
    local = [{'text': words[index]['text'], 'confidence': round(float(words[index]['ocr_confidence']), 2),
              'ocr_line_word_index': index,
              'bbox_pdf_points': [round(float(words[index][key]), 2) for key in ('x0', 'top', 'x1', 'bottom')]}
             for index in row_indices]
    slot = {'band_pdf_points': [box[1], box[3]], 'words': local,
            'column_observation': {'description': {'word_indices': [row_indices.index(index) for index in selected
                if index in row_indices], 'raw_ocr_text': cell['raw_text']}}}
    adapted = {'page_number': page_number, 'ocr_line_source': source, 'slots': [slot]}
    validate_line_source(adapted, source_sha256=observation['source_sha256'])
    if not selected or len(set(selected) & set(row_indices)) != len(selected):
        return None
    for word in words:
        overlaps = (word['x0'] < box[2] and word['x1'] > box[0] and
                    word['top'] < box[3] and word['bottom'] > box[1])
        contained = (box[0] <= word['x0'] < word['x1'] <= box[2] and
                     box[1] <= word['top'] < word['bottom'] <= box[3])
        if overlaps and not contained:
            return None  # Neither side of a centre-assigned edge proves ownership.
    result = description_line_reading(slot, adapted)
    if result is None or result['status'] != 'geometry_ordered_unverified':
        return None
    return {'mode': MODE, 'first_raw': cell['raw_text'], 'ordered_raw': result['ordered_ocr_text'],
            'source_page_word_indices': selected, 'source_word_sha256': source['words_sha256'],
            'source_line_references': deepcopy(result['ocr_line_references']),
            'source_crop_sha256': cell['crop_sha256']}
