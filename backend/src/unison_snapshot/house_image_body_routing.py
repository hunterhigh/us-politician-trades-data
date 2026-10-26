"""Route one image body directly under a source-observed native PTR table header.

This deliberately excludes arbitrary logos, signatures and overlapping text
layers. It preserves every native word; OCR cannot replace a native fact.
The parser obtains these objects from the hash-checked PDF. Saved consumers
reproduce cache consistency only, using the existing native-word trust boundary;
this module does not independently reopen or authenticate the archived PDF.
"""
from copy import deepcopy
import math

from .house import HouseIndexError

METHOD = 'native_image_body_ocr/v1'
SCHEMA = 'house-native-image-body-observation/v1'
WORD_TAG = '_house_image_body_ocr'


def _box(word):
    try:
        values = [word[key] for key in ('x0', 'top', 'x1', 'bottom')]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            return None
        return values if values[0] < values[2] and values[1] < values[3] else None
    except (KeyError, TypeError):
        return None


def _overlaps(left, right):
    # Touching borders are ambiguous too; do not erase a native border word.
    return not (left[2] < right[0] or right[2] < left[0] or left[3] < right[1] or right[3] < left[1])


def image_body_region(page: dict) -> list | None:
    """Require the existing eight-cell header and its directly attached image."""
    from .house_native_coverage import GEOMETRY_SCHEMA, _header_cells
    geometry = page.get('native_page_geometry')
    if (not page.get('words') or not isinstance(geometry, dict) or
            geometry.get('schema_version') != GEOMETRY_SCHEMA or
            geometry.get('width') != page.get('width') or geometry.get('height') != page.get('height') or
            not isinstance(geometry.get('rects'), list) or not isinstance(geometry.get('images'), list)):
        return None
    try:
        cells = _header_cells(page, geometry)
        if cells is None:
            return None
        left, right, top = cells[0][0], cells[-1][2], cells[0][3]
        images = [box for box in geometry['images'] if
            isinstance(box, list) and len(box) == 4 and
            all(type(v) in (int, float) and math.isfinite(v) for v in box) and
            abs(box[0] - left) <= 1 and abs(box[2] - right) <= 1 and abs(box[1] - top) <= 1 and
            0 <= box[0] < box[2] <= page['width'] and 0 <= box[1] < box[3] <= page['height'] and
            box[3] - box[1] > 20]
        if len(images) != 1:
            return None
        selected = images[0]
        # Additional overlapping image objects make the partition ambiguous.
        if any(other != selected and _overlaps(other, selected) for other in geometry['images']):
            return None
        boxes = [_box(word) for word in page['words']]
        if any(box is None or _overlaps(box, selected) for box in boxes):
            return None
        return list(selected)
    except (TypeError, KeyError, ValueError, IndexError):
        return None


def _merged_words(page, proof):
    if not isinstance(proof, dict) or proof.get('schema_version') != SCHEMA:
        raise HouseIndexError('House image body observation version is invalid')
    native, observed = proof.get('native_words'), proof.get('ocr_words')
    if (not isinstance(native, list) or not isinstance(observed, list) or
            any(not isinstance(word, dict) for word in [*native, *observed])):
        raise HouseIndexError('House image body word partitions are missing')
    if any(WORD_TAG in word or word.get('ocr_confidence') is not None for word in native):
        raise HouseIndexError('House image body native words contain OCR provenance')
    original = {**page, 'words': native}
    region = image_body_region(original)
    geometry = original.get('native_page_geometry', {})
    if (region is None or proof.get('region') != region or
            proof.get('source_sha256') != geometry.get('source_sha256') or
            proof.get('page_number') != geometry.get('page_number')):
        raise HouseIndexError('House image body source region cannot be replayed')
    selected, crossed = [], False
    for word in observed:
        box = _box(word)
        confidence = word.get('ocr_confidence')
        if (box is None or WORD_TAG in word or not isinstance(word.get('text'), str) or
                type(confidence) not in (int, float) or not 0 <= confidence <= 100 or
                not 0 <= box[0] < box[2] <= page['width'] or not 0 <= box[1] < box[3] <= page['height']):
            raise HouseIndexError('House image body OCR word geometry or confidence is invalid')
        inside = region[0] <= box[0] and box[2] <= region[2] and region[1] <= box[1] and box[3] <= region[3]
        if inside:
            selected.append({**word, WORD_TAG: True})
        elif _overlaps(box, region):
            crossed = True
    # A straddling word cannot be assigned by its centre or silently clipped.
    if crossed:
        selected = []
    return sorted([*deepcopy(native), *selected], key=lambda word: (word['top'], word['x0']))


def merge_image_body_page(native_page: dict, ocr_page: dict) -> dict:
    geometry = native_page['native_page_geometry']
    proof = {'schema_version': SCHEMA, 'source_sha256': geometry['source_sha256'],
             'page_number': geometry['page_number'], 'region': image_body_region(native_page),
             'native_words': deepcopy(native_page['words']), 'ocr_words': deepcopy(ocr_page['words'])}
    result = {**deepcopy(native_page), 'image_body_observation': proof}
    result['words'] = _merged_words(result, proof)
    return result


def validate_image_body_page(page: dict) -> None:
    if page.get('words') != _merged_words(page, page.get('image_body_observation')):
        raise HouseIndexError('House image body merged words differ from saved source partitions')
