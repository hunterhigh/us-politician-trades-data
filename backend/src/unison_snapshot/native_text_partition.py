"""Source-bound native mode0/mode3 observations, without fact qualification.

Original glyphs and physical rows are never deleted. The visible view is a
separate observation; unmatched hidden statements conservatively interrupt
association, whereas hidden financial fields affect their own physical row.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re

from .pdf_native_observer import observe_source_characters
from .native_source_visibility import CATALOG_KEYS, inspect_zero_three_native_page

PLAN_SCHEMA = 'native-text-partition-plan/v1'
SCHEMA = 'native-text-partition-observation/v1'
POLICY_VERSION = 'source-native-mode-zero-three/v1'
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
MAX_TOTAL_CHARACTERS = 200_000
MAX_ROWS = 5_000
MAX_TABLES_PER_PAGE = 32
MAX_GRID_LINES = 2_000
MAX_GRID_CELLS = 30_000
MAX_MEMBERSHIP_COMPARISONS = 200_000
MAX_PROOF_BYTES = 64 * 1024 * 1024


def _safe(value):
    if isinstance(value, dict):
        return {str(key):_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {'nonfinite_source_number':repr(value)}
    if value is None or type(value) in (str, bool, int, float):
        return value
    return {'source_object_type':type(value).__name__, 'value':str(value)}


def _compact(value):
    return ' '.join(str(value or '').split())


def _source(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('Native partition requires an exact source SHA256')
    path = Path(path)
    if not 0 < path.stat().st_size <= MAX_PDF_BYTES:
        raise ValueError('Native partition source exceeds byte budget')
    with path.open('rb') as stream:
        raw = stream.read(MAX_PDF_BYTES + 1)
    if len(raw) > MAX_PDF_BYTES or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Native partition source bytes differ from expected SHA256')
    return raw


def _inventory(document):
    pages, inventory, total, seen_hidden = [], [], 0, False
    if not 0 < len(document.pages) <= MAX_PAGES:
        return pages, inventory, None, ['native_partition_page_budget_exceeded']
    for number, page in enumerate(document.pages, 1):
        try:
            observed = observe_source_characters(page)
            chars, modes, flags, paint = observed
            total += len(chars)
            if total > MAX_TOTAL_CHARACTERS:
                raise ValueError('native_partition_total_character_budget_exceeded')
            hidden = 3 in modes
            seen_hidden = seen_hidden or hidden
            pages.append({'page_number':number, 'status':'observed', 'has_mode3':hidden,
                'character_count':len(chars), 'render_mode_counts':dict(sorted(Counter(map(str, modes)).items()))})
            inventory.append(observed)
        except Exception as error:
            reason = str(error)
            if not reason.startswith(('source_visibility_', 'native_partition_')):
                reason = 'native_partition_inventory_unavailable'
            pages.append({'page_number':number, 'status':'unknown', 'has_mode3':None, 'reason':reason})
            return pages, inventory, True if seen_hidden else None, [reason]
    return pages, inventory, seen_hidden, []


def _plan(raw, expected, document):
    pages, inventory, activated, reasons = _inventory(document)
    return {'schema_version':PLAN_SCHEMA, 'policy_version':POLICY_VERSION,
        'source_sha256':expected, 'source_byte_length':len(raw),
        'status':'unknown' if reasons else 'observed', 'activated':activated,
        'inventory_complete':not reasons, 'page_count':len(document.pages),
        'pages':pages, 'reasons':reasons}, inventory


def plan_native_text_partition(path, expected_sha256):
    """Observe modes before cache selection; unavailable never means no mode3."""
    raw = _source(path, expected_sha256)
    import pdfplumber
    try:
        with pdfplumber.open(BytesIO(raw)) as document:
            return _safe(_plan(raw, expected_sha256, document)[0])
    except Exception:
        return {'schema_version':PLAN_SCHEMA, 'policy_version':POLICY_VERSION,
            'source_sha256':expected_sha256, 'source_byte_length':len(raw),
            'status':'unknown', 'activated':None, 'inventory_complete':False,
            'page_count':None, 'pages':[], 'reasons':['native_partition_document_unavailable']}


def _box(char):
    return [char[key] for key in ('x0', 'top', 'x1', 'bottom')]


def _finite_box(box):
    return (isinstance(box, (tuple, list)) and len(box) == 4 and all(
        type(value) in (int, float) and math.isfinite(value) for value in box) and
        box[0] < box[2] and box[1] < box[3])


def _inside(box, cell):
    return (cell is not None and cell[0] <= box[0] and cell[1] <= box[1] and
            box[2] <= cell[2] and box[3] <= cell[3])


def _overlap(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _header(cells):
    return (len(cells) == 6 and _compact(cells[0]).lower() in {'#', 'no', 'no.'} and
            all(token in _compact(cells[index]).lower() for index, token in
                ((1,'description'), (2,'type'), (3,'date'), (4,'notification'), (5,'amount'))))


def _financial(cells):
    # Raw cells locate a financial slot; they do not authorize its values.
    return len(cells) == 6 and (bool(re.fullmatch(r'[0-9]+', _compact(cells[0]))) or
                              any(_compact(cells[index]) for index in (2, 3, 5))) and not _header(cells)


def _glyph_key(char):
    return (char.get('text'), char.get('fontname'), char.get('size'), tuple(char.get('matrix', ())))


def _lines(page, char_indices):
    return [{'text':line['text'], 'bbox':_box(line),
        'char_indices':[char_indices[id(char)] for char in line['chars']]}
        for line in page.extract_text_lines(return_chars=True)]


def _baseline_tables(page):
    """Keep the old borderless-first inventory on unaffected native pages."""
    from .oge_reports import _extract_borderless_transaction_tables, _looks_like_transaction_table
    borderless = _extract_borderless_transaction_tables(page)
    if borderless:
        return [{'raw':table, 'cells':table, 'grid':None, 'visible_grid':None, 'selected':True}
                for table in borderless]
    tables = page.find_tables()
    return [{'raw':table.extract(), 'cells':table.extract(), 'grid':[row.cells for row in table.rows],
        'visible_grid':[row.cells for row in table.rows],
        'selected':any(_header(row) for row in table.extract()) or _looks_like_transaction_table(table.extract())}
        for table in tables]


def _partition_tables(page, visible):
    original, projected = page.find_tables(), visible.find_tables()
    if len(original) > MAX_TABLES_PER_PAGE:
        raise ValueError('native_partition_table_budget_exceeded')
    output = []
    for table in original:
        grid = [row.cells for row in table.rows]
        matches = [other for other in projected if [row.cells for row in other.rows] == grid]
        raw = table.extract()
        selected = any(_header(row) or _financial(row) for row in raw)
        if len(matches) != 1:
            output.append({'raw':raw, 'cells':[['']*len(row) for row in raw], 'grid':grid,
                'visible_grid':None, 'selected':selected, 'reason':'native_partition_grid_projection_unresolved'})
        else:
            output.append({'raw':raw, 'cells':matches[0].extract(), 'grid':grid,
                'visible_grid':grid, 'selected':selected})
    return output


def _memberships(chars, tables):
    """Bound full glyph boxes, charging every candidate cell comparison."""
    slots = []
    for table_index, table in enumerate(tables):
        for row_index, cells in enumerate(table['grid'] or []):
            for cell_index, cell in enumerate(cells):
                if _finite_box(cell):
                    slots.append((cell[1], cell, (table_index, row_index, cell_index)))
    if len(slots) > MAX_GRID_CELLS:
        raise ValueError('native_partition_grid_cell_budget_exceeded')
    slots.sort(key=lambda value:value[0])
    tops = [item[0] for item in slots]
    result, crossings, count = {}, {}, 0
    for index, char in enumerate(chars):
        box = _box(char); found, touched = [], []
        if not _finite_box(box):
            crossings[index] = ['native_partition_glyph_box_unavailable']; continue
        for _, cell, location in slots[:bisect_right(tops, box[3])]:
            # Row intervals ending above a glyph are not cell candidates.
            count += 1
            if count > MAX_MEMBERSHIP_COMPARISONS:
                raise ValueError('native_partition_membership_budget_exceeded')
            if cell[3] <= box[1]:
                continue
            if _inside(box, cell):
                found.append(location)
            elif _overlap(box, cell):
                touched.append(location)
        if len(found) == 1 and not touched:
            result[index] = found[0]
        elif found or touched:
            crossings[index] = sorted(set(found + touched))
    return result, crossings, count


def _observe(raw, expected, document):
    plan, inventories = _plan(raw, expected, document)
    result = {'schema_version':SCHEMA, 'policy_version':POLICY_VERSION,
        'source_sha256':expected, 'source_byte_length':len(raw), 'plan':plan,
        'activated':plan['activated'], 'inventory_complete':plan['inventory_complete'],
        'status':'observed', 'pages':[], 'rows':[], 'inventory':[], 'raw_inventory':[],
        'scope_barriers_before':{}, 'row_reasons':{}, 'document_reasons':list(plan['reasons']),
        'cover_unmatched_hidden_regions':[], 'unknown_regions':[]}
    if not plan['inventory_complete']:
        result['status'] = 'unknown'
        return result
    document_supported = set(document.doc.catalog) <= CATALOG_KEYS
    pending_scope_barrier = False
    for number, (page, observed) in enumerate(zip(document.pages, inventories), 1):
        chars, modes, flags, paint = observed
        hidden = [index for index, mode in enumerate(modes) if mode == 3]
        visible_indices = [index for index, mode in enumerate(modes) if mode != 3]
        char_indices = {id(char):index for index, char in enumerate(page.chars)}
        profile = (inspect_zero_three_native_page(page) if hidden else
                   {'status':'unchanged_native', 'reason':None})
        if hidden and not document_supported:
            profile = {'status':'unknown', 'reason':'native_document_paint_state_unsupported'}
        page_reasons = [] if profile['status'] != 'unknown' else [profile['reason']]
        view = page.filter(lambda obj, by_id=char_indices, selected=set(visible_indices):
            obj.get('object_type') != 'char' or by_id[id(obj)] in selected)
        page_result = {'page_number':number, 'status':'unknown' if page_reasons else 'observed',
            'profile_status':profile['status'], 'reasons':page_reasons,
            'width':page.width, 'height':page.height, 'chars':chars, 'render_modes':modes,
            'visibility_flags':flags, 'paint':paint, 'visible_char_indices':visible_indices,
            'hidden_char_indices':hidden, 'raw_text':page.extract_text() or '',
            'visible_text':view.extract_text() or '', 'raw_lines':_lines(page, char_indices),
            'visible_lines':_lines(view, char_indices), 'tables':[], 'hidden_counterparts':{},
            'unmatched_hidden_indices':[], 'outside_table_hidden_indices':[]}
        if len(page.lines) > MAX_GRID_LINES:
            page_reasons.append('native_partition_grid_line_budget_exceeded')
        try:
            if page_reasons:
                # Never expose unproved raw native fields as a visible fallback.
                tables = []
                result['unknown_regions'].append({'page_number':number, 'reasons':list(page_reasons)})
                page_result['visible_text'] = ''
                page_result['visible_lines'] = []
            else:
                tables = _partition_tables(page, view) if hidden else _baseline_tables(page)
                if len(tables) > MAX_TABLES_PER_PAGE:
                    raise ValueError('native_partition_table_budget_exceeded')
                if hidden and number > 1 and not any(table['selected'] for table in tables):
                    result['unknown_regions'].append({'page_number':number,
                        'reasons':['native_partition_source_table_map_unavailable']})
                    result['status'] = 'partial'
                    pending_scope_barrier = True
            memberships, crossings, comparisons = _memberships(chars, tables)
            page_result['membership_comparison_count'] = comparisons
            pairs = {}
            for index, mode in enumerate(modes):
                if mode == 0:
                    pairs.setdefault(_glyph_key(chars[index]), []).append(index)
            for index in hidden:
                peers = pairs.get(_glyph_key(chars[index]), [])
                if len(peers) == 1:
                    page_result['hidden_counterparts'][str(index)] = peers[0]
                else:
                    page_result['unmatched_hidden_indices'].append(index)
            unmatched = set(page_result['unmatched_hidden_indices'])
            for table_index, table in enumerate(tables):
                table_reasons = [table['reason']] if table.get('reason') else []
                if hidden and table['selected'] and not any(_header(cells) for cells in table['cells']):
                    table_reasons.append('native_table_header_visibility_unknown')
                table_result = {**table, 'table_index':table_index, 'reasons':table_reasons, 'source_rows_indices':[]}
                for row_index, (raw_cells, cells) in enumerate(zip(table['raw'], table['cells'])):
                    if not table['selected']:
                        continue
                    if len(result['rows']) >= MAX_ROWS:
                        raise ValueError('native_partition_row_budget_exceeded')
                    index = len(result['rows'])
                    if pending_scope_barrier:
                        result['scope_barriers_before'][str(index)] = True
                        pending_scope_barrier = False
                    boxes = table['grid'][row_index] if table['grid'] is not None else None
                    members = [[i for i, location in memberships.items() if location == (table_index,row_index,col)]
                               for col in range(len(cells))]
                    hidden_members = [[i for i in values if modes[i] == 3] for values in members]
                    unpaired = [i for values in hidden_members for i in values if i in unmatched]
                    touched = [i for i, locations in crossings.items() if any(
                        isinstance(location, tuple) and location[:2] == (table_index,row_index) for location in locations)]
                    reasons = list(table_reasons)
                    if hidden and touched:
                        reasons.append('native_partition_cell_membership_unresolved')
                        if not _financial(raw_cells) and not _header(raw_cells):
                            result['scope_barriers_before'][str(index)] = True
                    financial = not _header(raw_cells) and (_financial(raw_cells) or _financial(cells))
                    if unpaired and financial:
                        reasons.append('native_hidden_financial_field_unresolved')
                    elif unpaired and not _header(raw_cells):
                        result['scope_barriers_before'][str(index)] = True
                        reasons.append('native_hidden_statement_association_unresolved')
                    if table_reasons and row_index == 0:
                        result['scope_barriers_before'][str(index)] = True
                    row = {'source_rows_index':index, 'page_number':number, 'table_index':table_index,
                        'row_index':row_index, 'raw_cells':list(map(_compact, raw_cells)),
                        'cells':list(map(_compact, cells)), 'cell_boxes':boxes,
                        'char_indices_by_cell':members,
                        'visible_indices_by_cell':[[i for i in values if modes[i] != 3] for values in members],
                        'hidden_indices_by_cell':hidden_members, 'unmatched_hidden_indices':unpaired,
                        'is_header':_header(raw_cells) or _header(cells), 'financial_slot':financial,
                        'reasons':sorted(set(reasons))}
                    result['rows'].append(row)
                    result['raw_inventory'].append([number, row['raw_cells']])
                    result['inventory'].append([number, row['cells']])
                    if reasons:
                        result['row_reasons'][str(index)] = row['reasons']
                    table_result['source_rows_indices'].append(index)
                page_result['tables'].append(table_result)
            unsupported_members = [index for index in unmatched if index in memberships and
                not tables[memberships[index][0]]['selected']]
            if unsupported_members:
                reason = 'native_hidden_table_association_unresolved'
                result['unknown_regions'].append({'page_number':number, 'reasons':[reason],
                    'char_indices':sorted(unsupported_members)})
                result['status'] = 'partial'
                for index in unsupported_members:
                    top = chars[index]['top']
                    later = [row for row in result['rows'] if row['page_number'] == number and
                        row['cell_boxes'] and any(cell and cell[3] > top for cell in row['cell_boxes'])]
                    if later:
                        first = min(later, key=lambda row:row['source_rows_index'])
                        result['scope_barriers_before'][str(first['source_rows_index'])] = True
                    else:
                        pending_scope_barrier = True
            outside = [index for index in hidden if index not in memberships and index not in crossings]
            page_result['outside_table_hidden_indices'] = outside
            # A hidden statement with no unique slot inside a table's physical
            # span cannot silently disappear. A footer below all grids does not
            # participate in association. No hidden text is interpreted here.
            for index in outside:
                if index not in unmatched:
                    continue
                box = _box(chars[index])
                targets = [row for row in result['rows'] if row['page_number'] == number and
                    row['cell_boxes'] and any(cell and _finite_box(cell) and
                        max(box[0], cell[0]) < min(box[2], cell[2]) and box[1] < cell[3]
                        for cell in row['cell_boxes'])]
                if targets:
                    first = min(targets, key=lambda row:row['source_rows_index'])
                    result['scope_barriers_before'][str(first['source_rows_index'])] = True
                    risk = 'native_hidden_statement_position_unresolved'
                    first['reasons'] = sorted(set(first['reasons'] + [risk]))
                    result['row_reasons'][str(first['source_rows_index'])] = first['reasons']
            if number == 1:
                result['cover_unmatched_hidden_regions'] = [{'char_index':index, 'bbox':_box(chars[index]),
                    'text':chars[index]['text']} for index in outside if index in unmatched]
        except Exception as error:
            reason = str(error)
            if not reason.startswith('native_partition_'):
                reason = 'native_partition_table_inventory_unavailable'
            page_reasons.append(reason)
            page_result['status'] = 'unknown'
            result['unknown_regions'].append({'page_number':number, 'reasons':[reason]})
            page_result['visible_text'] = ''
        page_result['text'] = page_result['visible_text']
        if page_reasons:
            page_result['status'] = 'unknown'
            page_result['text'] = page_result['visible_text'] = ''
            page_result['visible_lines'] = []
            pending_scope_barrier = True
            for row in result['rows']:
                if row['page_number'] == number:
                    row['reasons'] = sorted(set(row['reasons'] + page_reasons))
                    result['row_reasons'][str(row['source_rows_index'])] = row['reasons']
        if page_reasons or any(row['reasons'] for row in result['rows'] if row['page_number'] == number):
            result['status'] = 'partial'
        result['pages'].append(page_result)
    return result


def observe_native_text_partition(path, expected_sha256):
    """Return a fully replayable JSON source observation, without OCR."""
    raw = _source(path, expected_sha256)
    import pdfplumber
    try:
        with pdfplumber.open(BytesIO(raw)) as document:
            result = _safe(_observe(raw, expected_sha256, document))
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_PROOF_BYTES:
            raise ValueError('native_partition_proof_byte_budget_exceeded')
        return result
    except Exception as error:
        reason = str(error)
        if not reason.startswith('native_partition_'):
            reason = 'native_partition_document_unavailable'
        return {'schema_version':SCHEMA, 'policy_version':POLICY_VERSION, 'source_sha256':expected_sha256,
            'source_byte_length':len(raw), 'activated':None, 'inventory_complete':False, 'status':'unknown',
            'document_reasons':[reason], 'pages':[], 'rows':[], 'inventory':[], 'raw_inventory':[],
            'scope_barriers_before':{}, 'row_reasons':{}, 'cover_unmatched_hidden_regions':[], 'unknown_regions':[]}
