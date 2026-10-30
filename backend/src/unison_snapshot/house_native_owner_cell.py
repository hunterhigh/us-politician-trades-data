"""Account for an owner cell inside an already authenticated native source.

The caller must have reopened the original and bound the complete character,
font, paint and native page-role inventories. This component never accepts a
saved positive token and does not grant a financial qualification by itself.
"""
from __future__ import annotations

import math

from .house_native_coverage import _geometry, _header_cells, _in
from .house_revision_source_scope import (
    _Unknown, _charge, _inside, _light_row_background, _need, _overlaps,
)


VERSION = 'house-native-owner-cell-source-members/v1'


def _box(value):
    return [float(value[k]) for k in ('x0', 'top', 'x1', 'bottom')]


def prove_native_owner_cell(page, number, row, block, chars, occurrences, inventory, *, budget=None):
    """Bind source bands and permit only consumed owner glyphs or grid borders.

    A blank raw owner retains the existing blank-to-Self interpretation only
    when the entire physical owner cell has no native characters, words or
    effective glyph domains, and every other intersecting event is uniquely
    explained by an actual thin top/bottom table-border rectangle. Coordinates
    come from existing source roles; the target paint cannot define a mask.
    """
    result = {'schema_version': VERSION, 'page_number': number,
              'status': 'unknown', 'reason': None}
    budget = {'comparisons': 0} if budget is None else budget
    try:
        from .house_ptr import OWNER_CODES

        original_chars = chars['chars'] if isinstance(chars, dict) else chars
        words = page['words']
        _need(isinstance(original_chars, list) and isinstance(occurrences, list)
              and isinstance(inventory, list) and len(original_chars) == len(occurrences),
              'owner_source_character_inventory_differs')
        _need(block.get('page_number') == number, 'owner_source_block_page_differs')
        indices = block.get('word_indices')
        fields = block.get('field_word_indices')
        _need(isinstance(indices, list) and isinstance(fields, list)
              and len(indices) == len(set(indices)) and len(fields) == len(set(fields))
              and all(type(i) is int and 0 <= i < len(words) for i in indices)
              and set(fields) <= set(indices), 'owner_source_block_members_invalid')
        raw_owner = row.get('raw_cells', {}).get('owner')
        _need(raw_owner in OWNER_CODES, 'owner_source_code_unrecognized')
        geometry = page.get('native_page_geometry', {})
        geometry = _geometry(page, geometry.get('source_sha256'), number)
        _need(geometry is not None, 'owner_native_geometry_unavailable')
        header = _header_cells(page, geometry)
        _need(header is not None, 'owner_unique_eight_column_header_unavailable')
        left, right = header[0][0], header[-1][2]
        top = header[0][3]
        _charge(budget, len(geometry['rects']) * 2)
        sides = [sorted(set((round(r[1], 2), round(r[3], 2))
            for r in geometry['rects'] if r[2]-r[0] <= 1.5 and abs(r[0]-side) <= 1
            and r[3]-r[1] > 10 and r[3] > top+1)) for side in (left, right)]
        _need(bool(sides[0]) and len(sides[0]) == len(sides[1])
              and all(abs(a-c) <= 1 and abs(b-d) <= 1
                      for (a,b),(c,d) in zip(*sides)), 'owner_source_band_sides_unresolved')
        bands = []
        for start, end in sides[0]:
            start = max(start, top)
            if bands and start < bands[-1][1]-.8:
                bands[-1][1] = max(end, bands[-1][1])
            else:
                bands.append([start, end])
        selected = []
        represented = set()
        wanted = set(indices)
        _charge(budget, len(bands) * len(words))
        for start, end in bands:
            members = {i for i,w in enumerate(words) if _in(w, [left,start,right,end])}
            if members & wanted:
                _need(members <= wanted, 'owner_source_band_crosses_row_members')
                selected.append([start,end])
                represented.update(members)
        _need(bool(selected) and represented == wanted, 'owner_source_physical_members_not_closed')
        _need(all(abs(a[1]-b[0]) <= 1 for a,b in zip(selected,selected[1:])),
              'owner_source_bands_discontinuous')
        cell = [header[1][0], selected[0][0], header[1][2], selected[-1][1]]
        result.update(owner_cell=cell, source_band_bounds=selected, raw_owner=raw_owner)

        _charge(budget, len(words) + len(original_chars) + len(occurrences))
        owner_words = [i for i,w in enumerate(words) if _overlaps(cell, _box(w))]
        _need(all(i in wanted and i in fields and _inside(cell, _box(words[i])) for i in owner_words),
              'unconsumed_or_crossing_owner_source_word')
        owner_words.sort(key=lambda i:(round(float(words[i]['top']),1),float(words[i]['x0'])))
        owner_text = ' '.join(str(words[i]['text']).replace('\x00','') for i in owner_words).strip()
        _need(owner_text == raw_owner, 'owner_source_cell_differs_from_consumed_value')
        owner_chars = {i for i,c in enumerate(original_chars) if _overlaps(cell, _box(c))}
        represented_chars = set()
        _charge(budget, len(owner_words) * len(original_chars))
        for index in owner_words:
            box = _box(words[index])
            members = [i for i,c in enumerate(original_chars) if _overlaps(box, _box(c))]
            _need(all(_inside(box, _box(original_chars[i])) for i in members)
                  and ''.join(original_chars[i]['text'] for i in members) == words[index]['text']
                  and not (represented_chars & set(members)), 'owner_word_character_membership_unknown')
            represented_chars.update(members)
        _need(represented_chars == owner_chars, 'unconsumed_owner_source_character')
        own_events = set()
        no_ink_events = {}
        for index, occurrence in enumerate(occurrences):
            if occurrence.get('outer_device_pixels') is None:
                text = original_chars[index].get('text')
                _need(isinstance(text, str) and bool(text) and text.isspace()
                      and occurrence.get('quantized_control_domain') is None
                      and 'source_glyph_has_no_control_points' in occurrence.get('unresolved', []),
                      'owner_unlocated_nonblank_source_glyph')
                _need(occurrence.get('occurrence') == index
                      and occurrence['paint_event'] not in no_ink_events,
                      'owner_no_ink_glyph_membership_differs')
                no_ink_events[occurrence['paint_event']] = index
            if index in represented_chars:
                _need(_inside(cell, occurrence.get('outer_device_pixels')),
                      'consumed_owner_glyph_outside_source_cell')
                own_events.add(occurrence['paint_event'])
            elif _overlaps(cell, occurrence.get('outer_device_pixels')):
                raise _Unknown('unconsumed_effective_glyph_in_owner_cell')

        grid = []
        for rect in geometry['rects']:
            if (rect[3]-rect[1] <= 1.5 and rect[2]-rect[0] > 20
                and (round(rect[1],2) == round(cell[1],2)
                     or round(rect[3],2) == round(cell[3],2))):
                grid.append((rect, [math.floor(rect[0]),math.floor(rect[1]),
                                    math.ceil(rect[2]),math.ceil(rect[3])]))
        # The independently closed table census supplies these rectangles.
        # Background paint cannot choose a region in which to ignore glyphs:
        # the entire owner cell has already passed the character/glyph census.
        backgrounds = [(rect, [math.floor(rect[0]), math.floor(rect[1]),
                               math.ceil(rect[2]), math.ceil(rect[3])])
                       for rect in geometry['rects'] if rect[3]-rect[1] > 2]
        first_glyph = min(v['paint_event'] for v in occurrences)
        borders = []
        _charge(budget, len(inventory) * max(1, len(grid)))
        for event in inventory:
            domain = event.get('outer_device_pixels')
            if domain is None:
                _need(event.get('kind') == 'glyph' and event.get('event') in no_ink_events
                      and event.get('occurrence') == no_ink_events[event['event']],
                      'owner_unlocated_source_paint')
            if event['event'] in own_events or not _overlaps(cell, domain):
                continue
            matches = [rect for rect, boundary in grid if boundary == domain]
            underlays = [rect for rect, boundary in backgrounds if boundary == domain]
            if (_light_row_background(event) and event['event'] < first_glyph
                    and len(underlays) == 1):
                borders.append({'event': event['event'], 'source_rect': underlays[0],
                                'outer_device_pixels': domain, 'role': 'row_background'})
                continue
            _need(event.get('kind') == 'path' and event.get('source_ops') == ['re']
                  and event.get('stroke') is False and len(matches) == 1,
                  'unexplained_source_paint_in_owner_cell')
            borders.append({'event': event['event'], 'source_rect': matches[0],
                            'outer_device_pixels': domain})
        result.update(status='observed', owner_word_indices=owner_words,
                      owner_character_indices=sorted(owner_chars), boundary_events=borders,
                      interpretation='existing_blank_to_Self' if raw_owner == '' else 'existing_explicit_owner_code')
    except _Unknown as error:
        result['reason'] = str(error)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        result['reason'] = 'owner_source_inventory_invalid'
    return result
