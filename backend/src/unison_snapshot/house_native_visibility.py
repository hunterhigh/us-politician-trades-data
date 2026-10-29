"""Bounded source render-mode partition for the modern PTR image-body route.

Plan: keep the existing pdfminer characters, coordinates and word extraction.
Only an entirely mode-3 layer inside an independently located image body may
leave the native input. Its original characters and words remain in the receipt.
For a hidden layer, no image, mixed/straddling modes, clipping, transparency,
Forms or incomplete observations preserve readings but block qualification and
zero claims. Ordinary mode-0 Form/clip/annotation behavior is retained; this is
not a general proof of its visibility. Explicit active opacity risks are blocked.
Mode-0 text can leave the native input only when every overlapping body glyph
precedes the one independently located image. We OCR the final composite body;
we do not claim that the image is opaque or that every earlier glyph vanished.
Mixed paint order, partial overlaps and risky image states remain unknown.

The producer observes the hash-checked source used by parse_archived_pdf. Saved
receipts replay internal consistency without OCR, along the existing native
word-cache trust boundary; they are not independent source authentication.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from .house import HouseIndexError

SCHEMA = 'house-native-text-visibility/v1'
KEY = 'native_visibility_observation'
MODE = 'source_pdfminer_visibility_and_paint_order/v1'
MAX_CHARACTERS = 25_000
MAX_TOKENS = 100_000
BLOCKER = 'native_text_visibility_unknown'


def _sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _words(chars):
    from pdfplumber.utils import extract_words
    return extract_words(chars, x_tolerance=2, y_tolerance=2, extra_attrs=['size'])


def _source_characters(page):
    from .pdf_native_observer import observe_source_characters
    return observe_source_characters(page, max_characters=MAX_CHARACTERS, max_tokens=MAX_TOKENS)

def _overlap(left, right):
    return max(left[0], right[0]) < min(left[2], right[2]) and max(left[1], right[1]) < min(left[3], right[3])


def _box(item):
    return [item[key] for key in ('x0', 'top', 'x1', 'bottom')]


def _inside(box, region):
    return region[0] <= box[0] <= box[2] <= region[2] and region[1] <= box[1] < box[3] <= region[3]


def _paint_candidate(page, chars, modes, flags, original, paint):
    """Locate a whole-body pre-image layer; later native text stays native."""
    if chars is None or paint is None:
        return None
    from .house_image_body_routing import image_body_region
    for image in paint['images']:
        region = image['box']
        header = [word for word in original if not _overlap(_box(word), region)]
        observed = image_body_region({key: value for key, value in
            {**page, 'words': header}.items() if key != KEY})
        reasons = []
        if observed != region:
            # A duplicate/overlapping picture must not turn an identified
            # earlier-native body back into eligible native facts.
            from .house_native_coverage import _header_cells
            cells = _header_cells({**page, 'words': header}, page['native_page_geometry'])
            if cells is None or not (abs(region[0] - cells[0][0]) <= 1 and
                    abs(region[2] - cells[-1][2]) <= 1 and abs(region[1] - cells[0][3]) <= 1 and
                    region[3] - region[1] > 20):
                continue
            reasons.append('source_visibility_image_body_not_unique_or_ambiguous')
        indices = [index for index, char in enumerate(chars) if _overlap(_box(char), region)]
        if not indices or all(paint['character_orders'][index] > image['paint_order'] for index in indices):
            continue
        if not all(paint['character_orders'][index] < image['paint_order'] for index in indices):
            reasons.append('source_visibility_image_body_paint_order_mixed')
        if any(not _inside(_box(chars[index]), region) for index in indices) or any(
                _overlap(_box(word), region) and not _inside(_box(word), region) for word in original):
            reasons.append('source_visibility_native_text_crosses_image_body')
        if any(modes[index] != 0 for index in indices):
            reasons.append('source_visibility_image_body_render_modes_mixed')
        reasons.extend(reason for index in indices for reason in flags[index])
        reasons.extend(image['visibility_flags'])
        return {'region': region, 'indices': indices, 'reasons': sorted(set(reasons))}
    return None


def _decision(page, chars, modes, flags, paint, original, unsupported):
    """Pure saved-replay decision; no source read, rendering or recognition."""
    reasons = list(unsupported)
    if chars is None or modes is None:
        return original, 'unknown', sorted(set(reasons + ['source_visibility_inventory_incomplete'])), None
    if _words(chars) != original:
        return original, 'unknown', sorted(set(reasons + ['source_visibility_word_projection_differs'])), None
    candidate = _paint_candidate(page, chars, modes, flags, original, paint)
    if candidate is not None and any(modes[index] == 0 for index in candidate['indices']):
        reasons.extend(candidate['reasons'])
        if any(mode != 0 for mode in modes):
            reasons.append('source_visibility_other_hidden_layer_unresolved')
        if any('source_visibility_active_graphics_state_unsupported' in items for items in flags):
            reasons.append('source_visibility_active_graphics_state_unsupported')
        if reasons:
            return original, 'unknown', sorted(set(reasons)), None
        selected = set(candidate['indices'])
        projected = _words([char for index, char in enumerate(chars) if index not in selected])
        from .house_image_body_routing import image_body_region
        if image_body_region({key: value for key, value in {**page, 'words': projected}.items() if key != KEY}) != candidate['region']:
            return original, 'unknown', ['source_visibility_image_body_projection_incomplete'], None
        return projected, 'image_body_earlier_native_partitioned', [], candidate['region']
    if any(type(mode) is not int or mode not in (0, 3) for mode in modes):
        reasons.append('source_visibility_render_mode_unsupported')
    reasons.extend(reason for items in flags for reason in items)
    hidden = [char for char, mode in zip(chars, modes) if mode == 3]
    if not hidden:
        return original, 'unknown', sorted(set(reasons + ['source_visibility_no_supported_hidden_layer'])), None
    visible = _words([char for char, mode in zip(chars, modes) if mode == 0])
    from .house_image_body_routing import image_body_region
    region = image_body_region({key: value for key, value in {**page, 'words': visible}.items() if key != KEY})
    if region is None:
        reasons.append('source_visibility_hidden_layer_without_unique_image_body')
    elif any(not all(type(char.get(key)) in (int, float) and math.isfinite(char[key])
                     for key in ('x0', 'top', 'x1', 'bottom')) or not (
        region[0] <= char['x0'] <= char['x1'] <= region[2] and
        region[1] <= char['top'] < char['bottom'] <= region[3]) for char in hidden):
        reasons.append('source_visibility_hidden_character_crosses_image_body')
    if region is not None:
        matching = [image for image in paint['images'] if image['box'] == region]
        if len(matching) != 1:
            reasons.append('source_visibility_image_event_mapping_incomplete')
        else:
            reasons.extend(matching[0]['visibility_flags'])
    if reasons:
        return original, 'unknown', sorted(set(reasons)), None
    return visible, 'image_body_hidden_layer_partitioned', [], region


def observe_visibility(source_page, page, source_sha, number):
    """Observe source modes; ordinary visible input remains byte-equivalent."""
    if not page['words']:
        return page
    chars = modes = flags = paint = None
    reasons = []
    try:
        if len(source_page.chars) > MAX_CHARACTERS:
            reasons.append('source_visibility_character_budget_exceeded')
        else:
            chars, modes, flags, paint = _source_characters(source_page)
    except Exception as exc:
        # An unavailable observation never silently falls back to eligible
        # native fields. Keep the old readings beside the explicit unknown.
        reasons.append(str(exc) if isinstance(exc, ValueError) and str(exc).startswith('source_visibility_') else
                       'source_visibility_inventory_unavailable')
    # A Form, clip or annotation by itself is not a new defect in the existing
    # native parser. Retain its established mode-0 behavior. These structures
    # do prevent extending the new hidden-layer partition through them.
    opacity_risk = flags is not None and any(
        'source_visibility_active_graphics_state_unsupported' in items for items in flags)
    candidate = _paint_candidate(page, chars, modes, flags, page['words'], paint)
    if not reasons and chars is not None and not opacity_risk and candidate is None and all(type(mode) is int and mode == 0 for mode in modes):
        return page
    if source_page.rotation != 0 or tuple(source_page.bbox) != (0, 0, page['width'], page['height']):
        reasons.append('source_visibility_page_frame_unsupported')
    projected, status, reasons, region = _decision(page, chars, modes, flags, paint, page['words'], reasons)
    proof = {'schema_version': SCHEMA, 'mode': MODE, 'source_sha256': source_sha,
        'page_number': number, 'page_size': [page['width'], page['height']],
        'source_characters': chars, 'source_render_modes': modes,
        'source_character_visibility_flags': flags,
        'source_paint': paint,
        'source_characters_sha256': _sha([chars, modes, flags, paint]),
        'original_words': deepcopy(page['words']), 'unsupported_reasons': sorted(set(reasons)),
        'status': status, 'region': region,
        'trust_boundary': 'saved_native_word_consistency_not_independent_source_authentication'}
    return {**page, 'words': projected, KEY: proof}


def validate_visibility(page, source_sha, number):
    proof = page.get(KEY)
    if proof is None:
        return
    try:
        chars, modes, flags, paint = (proof['source_characters'], proof['source_render_modes'],
                                     proof['source_character_visibility_flags'], proof['source_paint'])
        if (proof['schema_version'] != SCHEMA or proof['mode'] != MODE or
                proof['source_sha256'] != source_sha or proof['page_number'] != number or
                proof['page_size'] != [page['width'], page['height']] or
                proof['source_characters_sha256'] != _sha([chars, modes, flags, paint]) or
                (chars is None) != (modes is None) or (chars is None) != (flags is None) or (chars is None) != (paint is None) or
                (chars is not None and (not isinstance(chars, list) or not isinstance(modes, list) or
                 not isinstance(flags, list) or len(chars) != len(modes) or len(chars) != len(flags) or
                 any(not isinstance(items, list) or any(not isinstance(item, str) for item in items) for items in flags) or
                 len(chars) > MAX_CHARACTERS)) or
                not isinstance(proof['unsupported_reasons'], list) or
                any(not isinstance(reason, str) for reason in proof['unsupported_reasons'])):
            raise ValueError('source visibility binding differs')
        if chars is not None:
            orders = paint['character_orders']; images = paint['images']; count = paint['paint_event_count']
            if (type(count) is not int or not 0 < count <= MAX_TOKENS or len(orders) != len(chars) or
                    any(type(order) is not int or not 0 < order <= count for order in orders) or
                    not isinstance(images, list) or len(images) > MAX_TOKENS or
                    any(type(image['paint_order']) is not int or not 0 < image['paint_order'] <= count or
                        not isinstance(image['visibility_flags'], list) or
                        any(not isinstance(flag, str) for flag in image['visibility_flags']) or
                        len(image['box']) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in image['box'])
                        for image in images) or
                    len({image['paint_order'] for image in images}) != len(images) or
                    set(orders) & {image['paint_order'] for image in images} or
                    sorted(image['box'] for image in images) != sorted(page['native_page_geometry']['images'])):
                raise ValueError('source visibility paint inventory differs')
        projected, status, reasons, region = _decision(page, chars, modes, flags, paint,
            proof['original_words'], proof['unsupported_reasons'])
        # Image OCR words are a separate saved partition, never native words.
        native = page.get('image_body_observation', {}).get('native_words', page['words'])
        if (projected != native or proof['status'] != status or proof['region'] != region or
                proof['unsupported_reasons'] != reasons):
            raise ValueError('source visibility projection differs')
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HouseIndexError('House native visibility observation cannot be reproduced') from exc


def unknown(page):
    return page.get(KEY, {}).get('status') == 'unknown'


def affects_row(page, bounds):
    """Unknown source text blocks its row; unlocated risk blocks the page."""
    if not unknown(page):
        return False
    proof = page[KEY]
    chars, modes, flags = (proof['source_characters'], proof['source_render_modes'],
                           proof['source_character_visibility_flags'])
    if chars is None or any(reason in proof['unsupported_reasons'] for reason in (
            'source_visibility_page_frame_unsupported', 'source_visibility_word_projection_differs',
            'source_visibility_token_budget_exceeded')):
        return True
    # Form/clip tags deny the NEW partition; they are not proof that an
    # unrelated ordinary mode-0 native field is invisible. Opacity risk is.
    bad = [char for char, mode, items in zip(chars, modes, flags) if mode != 0 or
           'source_visibility_active_graphics_state_unsupported' in items]
    candidate = _paint_candidate(page, chars, modes, flags, proof['original_words'], proof['source_paint'])
    if candidate is not None and any(modes[index] == 0 for index in candidate['indices']):
        bad.extend(chars[index] for index in candidate['indices'])
    if not bad:
        return True
    return any(max(bounds[0], char['x0']) < min(bounds[2], char['x1']) and
               max(bounds[1], char['top']) < min(bounds[3], char['bottom']) for char in bad)


def format_anchors_visible(first_page, document_id):
    """Reuse the existing modern title/filing-ID rule, without hidden chars.

    This does not invent a new signature/style/identity requirement. Ordinary
    mode-0 Form and clipping behavior retains its established trust boundary.
    """
    if not unknown(first_page):
        return True
    proof = first_page[KEY]
    if proof['source_characters'] is None:
        return False
    words = _words([char for char, mode, flags in zip(proof['source_characters'],
        proof['source_render_modes'], proof['source_character_visibility_flags']) if mode == 0 and
        'source_visibility_active_graphics_state_unsupported' not in flags])
    text = ' '.join(' '.join(str(word['text']).replace('\x00', '').split()) for word in words)
    compact = re.sub(r'[^A-Z]', '', text.upper())
    heading = [str(word['text']).strip() for word in words if float(word.get('size', 0)) >= 18]
    return f'#{document_id}' in text and ('PERIODICTRANSACTIONREPORT' in compact or heading[:3] == ['P', 'T', 'R'])
