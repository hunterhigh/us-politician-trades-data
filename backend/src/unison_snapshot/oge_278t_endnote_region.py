"""Source-replayed referential notes; their financial effect stays unknown.

This reads a bounded PART/#/ENDNOTE grid and exact original text members. It
does not interpret a note, deduplicate transactions, or change any fact.
"""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
import re

from .oge_278t_native_objects import digest, inspect_strict_native_objects
from .oge_278t_cover_role import _styled_chars
from .oge_278t_summary_region import observe_native_summary_region

REGION_RULE = 'oge-278t-native-referential-endnotes/v1'
MAX_PAGES = 32
MAX_DOCUMENT_CHARACTERS = 100_000
MAX_ROWS = 128
MAX_TARGETS = 4096
MAX_GEOMETRY_COMPARISONS = 500_000
HEADERS = ('#', 'DESCRIPTION', 'TYPE', 'DATE', 'NOTIFICATION', 'AMOUNT')


def _box(chars, indices):
    return [min(chars[i]['x0'] for i in indices), min(chars[i]['top'] for i in indices),
            max(chars[i]['x1'] for i in indices), max(chars[i]['bottom'] for i in indices)]


def _inside(box, region):
    return region[0] <= box[0] and region[1] <= box[1] and box[2] <= region[2] and box[3] <= region[3]


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _source(page, *, parent_reference=False):
    sizes = (10, 14)
    if parent_reference:
        # This only widens the low-level size admission for literal source
        # spaces. All visible members still have the original 10/14pt lane;
        # no glyph or geometry is discarded or normalized.
        sizes += tuple({c['size'] for c in page.chars if c.get('text') == ' '
                        and type(c.get('size')) in (int,float) and math.isfinite(c['size'])
                        and c['size'] > 0})
    inventory = inspect_strict_native_objects(page, allowed_sizes=sizes, allow_square_linecaps=True)
    if inventory['status'] != 'observed':
        raise ValueError(inventory['reason'])
    chars, runs, cursor = inventory['chars'], [], 0
    raw = b''.join(run['raw'] for run in inventory['text_run_records'])
    if raw.decode('ascii') != ''.join(c['text'] for c in chars):
        raise ValueError('endnote_source_run_projection_differs')
    if parent_reference:
        for index, char in enumerate(chars):
            if not any(abs(char['size']-size) <= 1e-5 for size in (10,14)):
                if char['text'] != ' ' or raw[index] != 0x20:
                    raise ValueError('reference_nonspace_character_style_unsupported')
    for index, run in enumerate(inventory['text_run_records']):
        indices = list(range(cursor, cursor+len(run['raw'])))
        cursor += len(indices)
        if not indices:
            continue
        runs.append({'run_index': index, 'raw_text': run['raw'].decode('ascii'),
            'character_indices': indices, 'show_operator_indices': run['show_operator_indices'],
            'bbox_pdf_points': _box(chars, indices)})
    return inventory, runs


def _run_refs(runs, indices):
    selected, refs = set(indices), []
    for run in runs:
        overlap = selected & set(run['character_indices'])
        if overlap:
            refs.append({'run_index': run['run_index'],
                'show_operator_indices': run['show_operator_indices'],
                'character_indices': sorted(overlap)})
    return refs


def _text_lines(chars, indices):
    lines = {}
    for index in indices:
        char = chars[index]
        lines.setdefault((round(char['top'], 5), round(char['bottom'], 5)), []).append(index)
    result = []
    for span, members in sorted(lines.items()):
        if members != sorted(members, key=lambda i: chars[i]['x0']):
            raise ValueError('endnote_source_text_order_unsupported')
        result.append(''.join(chars[i]['text'] for i in members))
    return result


def _source_digest(inventory, runs):
    return digest({'characters': _styled_chars(inventory['chars']),
        'operators': inventory['operators'], 'strokes': inventory['source_stroke_records'],
        'line_caps': inventory['line_cap_operations'], 'runs': runs})


def _number_inventory(inventory, runs, anchors, header_bottom, end):
    """Read complete independently bounded number cells, not numeric Tj runs."""
    strokes, chars = inventory['source_stroke_records'], inventory['chars']
    number_anchor, description_anchor = anchors['#'], anchors['DESCRIPTION']
    horizontal = [(i, stroke['bbox_pdf_points']) for i, stroke in enumerate(strokes)
        if stroke['bbox_pdf_points'][1] == stroke['bbox_pdf_points'][3]
        and header_bottom < stroke['bbox_pdf_points'][1] < end
        and stroke['bbox_pdf_points'][0] <= number_anchor[0]
        and number_anchor[2] <= stroke['bbox_pdf_points'][2]]
    ys = sorted(set(box[1] for _, box in horizontal))
    if len(ys) < 2:
        raise ValueError('reference_number_column_physical_grid_unknown')
    vertical = [stroke['bbox_pdf_points'] for stroke in strokes
        if stroke['bbox_pdf_points'][0] == stroke['bbox_pdf_points'][2]
        and stroke['bbox_pdf_points'][1] <= ys[0] and ys[-1] <= stroke['bbox_pdf_points'][3]]
    lefts = {box[0] for box in vertical if 0 <= number_anchor[0]-box[0] <= 20}
    rights = {box[0] for box in vertical if 0 <= description_anchor[0]-box[0] <= 20
              and number_anchor[2] < box[0]}
    targets, cells, issues, comparisons = [], [], [], 0
    for lo, hi in zip(ys, ys[1:]):
        # Adjacent source-rule segments establish the first column even on
        # the original borderless form. Full-width rules instead require
        # independent vertical boundaries spanning the complete grid.
        endpoints = [{(box[0], box[2]) for _, box in horizontal if box[1] == y
                      and 0 <= number_anchor[0]-box[0] <= 20
                      and 0 <= description_anchor[0]-box[2] <= 20}
                     for y in (lo, hi)]
        bounds = endpoints[0] & endpoints[1]
        bounds |= {(left, right) for left in lefts for right in rights if left < right}
        if len(bounds) != 1:
            issues.append('reference_number_cell_boundaries_unknown')
            continue
        left, right = next(iter(bounds)); cell = [left, lo, right, hi]
        comparisons += len(chars)+len(strokes)
        if comparisons > MAX_GEOMETRY_COMPARISONS:
            raise ValueError('reference_geometry_comparison_budget_exceeded')
        members = [i for i, c in enumerate(chars)
                   if _overlaps([c[k] for k in ('x0','top','x1','bottom')], cell)]
        record = {'bbox_pdf_points': cell, 'character_indices': members, 'status': 'unknown'}
        cells.append(record)
        if any(not _inside([chars[i][k] for k in ('x0','top','x1','bottom')], cell) for i in members):
            issues.append('reference_number_cell_character_crosses_boundary'); continue
        paint_ok = True
        for stroke in strokes:
            if not _overlaps(stroke['outer_bbox_pdf_points'], cell):
                continue
            box = stroke['bbox_pdf_points']
            horizontal_border = (box[1] == box[3] and box[1] in (lo, hi)
                                 and box[0] <= right and left <= box[2])
            vertical_border = (box[0] == box[2] and box[0] in (left, right)
                               and box[1] <= ys[0] and ys[-1] <= box[3])
            if not (horizontal_border or vertical_border) or any(_overlaps(
                    stroke['outer_bbox_pdf_points'], [chars[i][k] for k in ('x0','top','x1','bottom')])
                    for i in members):
                paint_ok = False; break
        if not paint_ok:
            issues.append('reference_number_cell_paint_unresolved'); continue
        ordered = sorted(members, key=lambda i: chars[i]['x0'])
        if not ordered:
            record.update(status='observed', raw_text='', printed_number=None)
            continue
        first = chars[ordered[0]]
        contiguous = all(round(chars[a]['x1'],5) == round(chars[b]['x0'],5)
                         for a,b in zip(ordered,ordered[1:]))
        same_line = all(round(chars[i]['top'],5) == round(first['top'],5) and
                        round(chars[i]['bottom'],5) == round(first['bottom'],5) and
                        chars[i]['fontname'] == first['fontname'] and chars[i]['size'] == first['size']
                        for i in ordered)
        text = ''.join(chars[i]['text'] for i in ordered)
        number = text.strip()
        record['raw_text'] = text
        if not contiguous or not same_line or (number and re.fullmatch(r'[1-9][0-9]{0,3}',number) is None):
            issues.append('reference_number_cell_layout_or_text_unresolved'); continue
        record.update(status='observed', printed_number=number or None)
        if number:
            targets.append({'band_pdf_points':[lo,hi], 'printed_number':number,
                'number_cell_bbox_pdf_points':cell, 'number_character_indices':ordered,
                'number_source_run_refs':_run_refs(runs,ordered)})
    # A number outside the reconstructed physical bands cannot disappear just
    # because no Tj run was chosen as an anchor. The known source whitespace
    # separator after the last grid line carries no printed numeral.
    if cells:
        strip = [min(c['bbox_pdf_points'][0] for c in cells), header_bottom,
                 max(c['bbox_pdf_points'][2] for c in cells), end]
        accounted = {i for cell in cells for i in cell['character_indices']}
        if any(i not in accounted and c['text'].strip() and _overlaps(
                [c[k] for k in ('x0','top','x1','bottom')], strip) for i,c in enumerate(chars)):
            issues.append('reference_number_column_character_outside_physical_cells')
    return targets, cells, issues, comparisons


def _target_scope(page, page_number, source_sha256, local_members, summary, cover_context):
    """Inventory printed source locators, never a parser version or raw ID."""
    pages = page.pdf.pages
    if len(pages) > MAX_PAGES:
        return [], ['reference_document_page_budget_exceeded'], None
    targets, reasons, records, total, comparisons = [], [], [], 0, 0
    from .oge_278t_page_roles import observe_native_page_role
    for number, candidate in enumerate(pages, 1):
        try:
            # A proven administrative profile has its own complete font/style
            # contract. Do not prefilter it with the transaction 10/14pt lane.
            role = observe_native_page_role(candidate, number, source_sha256=source_sha256,
                                            cover_identity_context=cover_context)
            if role['status'] == 'non_table':
                total += role['native_character_count']
                if total > MAX_DOCUMENT_CHARACTERS:
                    raise ValueError('reference_document_character_budget_exceeded')
                records.append({'page_number':number,'page_role':role})
                continue
            inventory, runs = _source(candidate, parent_reference=True)
            total += len(inventory['chars'])
            if total > MAX_DOCUMENT_CHARACTERS:
                raise ValueError('reference_document_character_budget_exceeded')
            chars = inventory['chars']
            selected = {label: [run for run in runs if run['raw_text'] == label] for label in HEADERS}
            # The distinct Endnote table has its own '#'. Only the number
            # anchor aligned with the complete financial header participates.
            if len(selected['DESCRIPTION']) == 1:
                header_top = selected['DESCRIPTION'][0]['bbox_pdf_points'][1]
                selected['#'] = [run for run in selected['#']
                                 if abs(run['bbox_pdf_points'][1]-header_top) < 32]
            if not all(len(values) == 1 for values in selected.values()):
                note_only = (number == page_number and all(i in local_members or
                    c['top'] >= summary['region_bbox_pdf_points'][1] or c['text'].isspace()
                    for i, c in enumerate(chars)))
                if not note_only:
                    raise ValueError('reference_competing_source_page_not_explained')
                records.append({'page_number': number, 'inventory_sha256': _source_digest(inventory, runs),
                                'page_role': 'note_only'})
                continue
            anchors = {label: values[0]['bbox_pdf_points'] for label, values in selected.items()}
            if ([anchors[label][0] for label in HEADERS] != sorted(set(anchors[label][0] for label in HEADERS))
                    or max(box[1] for box in anchors.values())-min(box[1] for box in anchors.values()) > 32):
                raise ValueError('reference_transaction_header_ambiguous')
            header_bottom = max(box[3] for box in anchors.values())
            end = min([run['bbox_pdf_points'][1] for run in runs if run['raw_text'] == 'Endnotes'
                       and run['bbox_pdf_points'][1] > header_bottom] or [candidate.height])
            found, number_cells, issues, cost = _number_inventory(inventory,runs,anchors,header_bottom,end)
            comparisons += cost
            if comparisons > MAX_GEOMETRY_COMPARISONS:
                raise ValueError('reference_geometry_comparison_budget_exceeded')
            reasons.extend(f'page_{number}:{issue}' for issue in sorted(set(issues)))
            for target in found:
                targets.append({'source_sha256': source_sha256, 'page_number': number, **target})
                if len(targets) > MAX_TARGETS:
                    raise ValueError('reference_target_inventory_budget_exceeded')
            records.append({'page_number': number, 'inventory_sha256': _source_digest(inventory, runs),
                            'number_cells':number_cells})
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
            reasons.append(f'page_{number}:{error}')
            if 'budget_exceeded' in str(error):
                break
    return targets, reasons, digest({'pages': records, 'targets': targets, 'reasons': reasons})


def observe_native_endnote_region(page, page_number, *, source_sha256, cover_identity_context=None):
    base = {'rule_version': REGION_RULE, 'source_sha256': source_sha256, 'page_number': page_number,
        'status': 'unknown', 'role': None, 'coordinate_frame': 'pdf_points', 'page_coverage': 'partial',
        'financial_effect': 'unknown', 'financial_fact_qualification': False}
    try:
        if (not isinstance(source_sha256, str) or re.fullmatch(r'[0-9a-f]{64}', source_sha256) is None
                or type(page_number) is not int or page_number < 1):
            raise ValueError('endnote_region_source_binding_unavailable')
        inventory, runs = _source(page)
        chars = inventory['chars']
        summary = observe_native_summary_region(page, page_number, source_sha256=source_sha256)
        if summary['status'] != 'observed':
            raise ValueError('endnote_following_region_boundary_unverified')
        labels = {text: [run for run in runs if run['raw_text'] == text]
                  for text in ('Endnotes', 'PART', '#', 'ENDNOTE')}
        if any(len(labels[text]) != 1 for text in ('Endnotes', 'PART', 'ENDNOTE')):
            raise ValueError('endnote_complete_header_not_unique')
        title, part, note = labels['Endnotes'][0], labels['PART'][0], labels['ENDNOTE'][0]
        number = [run for run in labels['#'] if round(run['bbox_pdf_points'][1], 5) ==
                  round(part['bbox_pdf_points'][1], 5)]
        if len(number) != 1 or round(part['bbox_pdf_points'][1], 5) != round(note['bbox_pdf_points'][1], 5):
            raise ValueError('endnote_complete_header_not_unique')
        headers = [part, number[0], note]
        header_bottom = max(run['bbox_pdf_points'][3] for run in headers)
        if not title['bbox_pdf_points'][3] < min(run['bbox_pdf_points'][1] for run in headers):
            raise ValueError('endnote_title_header_order_unknown')
        groups = {}
        for index, stroke in enumerate(inventory['source_stroke_records']):
            box = stroke['bbox_pdf_points']
            if box[1] == box[3] and header_bottom < box[1] < summary['region_bbox_pdf_points'][1]:
                groups.setdefault(box[1], []).append((index, box))
        if not 2 <= len(groups) <= MAX_ROWS+1:
            raise ValueError('endnote_source_row_inventory_unavailable')
        ys, edges, owned_paint = sorted(groups), None, []
        for y in ys:
            segments = sorted(groups[y], key=lambda item: item[1][0])
            candidate = sorted(set(v for _, box in segments for v in (box[0], box[2])))
            if (len(segments) != 3 or len(candidate) != 4 or
                    any(box[0] != candidate[i] or box[2] != candidate[i+1]
                        for i, (_, box) in enumerate(segments)) or edges is not None and candidate != edges):
                raise ValueError('endnote_three_column_source_rules_ambiguous')
            edges = candidate; owned_paint.extend(index for index, box in segments)
        bands = [[lo, hi] for lo, hi in zip(ys, ys[1:])]
        for col, run in enumerate(headers):
            if not _inside(run['bbox_pdf_points'], [edges[col], title['bbox_pdf_points'][3], edges[col+1], ys[0]]):
                raise ValueError('endnote_header_cell_geometry_unknown')
        lower = max(inventory['source_stroke_records'][i]['outer_bbox_pdf_points'][3] for i in owned_paint)
        region = [0., title['bbox_pdf_points'][1], float(page.width), lower]
        if lower >= summary['region_bbox_pdf_points'][1]:
            raise ValueError('endnote_source_region_overlaps_following_role')
        title_ids = set(title['character_indices'])
        header_ids = set(i for run in headers for i in run['character_indices'])
        members = {i for i, c in enumerate(chars) if _overlaps([c[k] for k in ('x0','top','x1','bottom')], region)}
        if len(members)*len(bands)*3 > MAX_GEOMETRY_COMPARISONS:
            raise ValueError('endnote_geometry_comparison_budget_exceeded')
        cells = [[[] for _ in range(3)] for _ in bands]
        for index in sorted(members-title_ids-header_ids):
            box = [chars[index][key] for key in ('x0', 'top', 'x1', 'bottom')]
            locations = [(r, c) for r, (lo, hi) in enumerate(bands) for c in range(3)
                         if _inside(box, [edges[c], lo, edges[c+1], hi])]
            if len(locations) != 1:
                raise ValueError('endnote_source_character_crosses_or_escapes_cell')
            r, c = locations[0]; cells[r][c].append(index)
        if not title_ids | header_ids <= members:
            raise ValueError('endnote_header_membership_incomplete')
        # Never silently truncate a continuation below the final printed rule.
        if any(c['text'].strip() and lower < c['bottom'] and c['top'] < summary['region_bbox_pdf_points'][1]
               for c in chars):
            raise ValueError('endnote_source_text_continues_outside_grid')
        for index, stroke in enumerate(inventory['source_stroke_records']):
            domain = stroke['outer_bbox_pdf_points']
            if _overlaps(domain, region) and (index not in owned_paint or not _inside(domain, region)):
                raise ValueError('endnote_competing_or_crossing_source_paint')
            if index in owned_paint and any(_overlaps(domain, [chars[i][k] for k in ('x0','top','x1','bottom')]) for i in members):
                raise ValueError('endnote_source_rule_overlaps_text')
        notes = []
        for row_index, row_cells in enumerate(cells):
            lines = [_text_lines(chars, indices) for indices in row_cells]
            if any(not cell or not ''.join(cell).strip() for cell in lines):
                raise ValueError('endnote_complete_source_cells_unavailable')
            part_text, number_text = '\n'.join(lines[0]), '\n'.join(lines[1])
            printed = number_text if re.fullmatch(r'[1-9][0-9]{0,3}', number_text) else None
            notes.append({'part': part_text, 'raw_number': number_text, 'printed_number': printed,
                'raw_text_lines': lines[2], 'raw_text': '\n'.join(lines[2]),
                'band_pdf_points': bands[row_index],
                'cells': [{'role': role, 'character_indices': indices,
                           'source_run_refs': _run_refs(runs, indices)}
                          for role, indices in zip(('part','number','endnote'), row_cells)]})
        context_hash = digest(cover_identity_context) if cover_identity_context is not None else None
        targets, reasons, scope_hash = _target_scope(page, page_number, source_sha256, members,
                                                     summary, cover_identity_context)
        for note in notes:
            found = [target for target in targets if target['printed_number'] == note['printed_number']]
            reason = ('unsupported_reference_part' if note['part'] != 'Transactions' else
                      'unsupported_reference_number' if note['printed_number'] is None else
                      'document_reference_scope_unknown' if reasons else
                      'target_number_missing' if not found else
                      'target_number_not_unique' if len(found) != 1 else None)
            note['reference'] = ({'status': 'unknown', 'reason': reason, 'candidate_count': len(found)}
                                 if reason else {'status': 'source_bound', 'target': found[0]})
        return {**base, 'status': 'observed', 'role': 'native_referential_endnotes', 'reason': None,
            'region_bbox_pdf_points': region, 'column_edges_pdf_points': edges, 'row_bands_pdf_points': bands,
            'title_character_indices': sorted(title_ids), 'header_character_indices': sorted(header_ids),
            'title_source_run_refs': _run_refs(runs, title_ids), 'header_source_run_refs': _run_refs(runs, header_ids),
            'region_character_indices': sorted(members), 'region_stroke_indices': sorted(owned_paint),
            'source_object_inventory_sha256': _source_digest(inventory, runs), 'notes': notes,
            'reference_scope_status': 'unknown' if reasons else 'observed', 'reference_scope_reasons': reasons,
            'reference_scope_sha256': scope_hash,
            'reference_cover_context_used': cover_identity_context is not None,
            'reference_cover_context_sha256': context_hash, 'recognizer_started': False}
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as error:
        return {**base, 'reason': str(error)}


def replay_native_endnote_region(path, proof, *, source_sha256, page_number, cover_identity_context=None):
    from .oge_reports import MAX_PDF_BYTES
    import pdfplumber
    if not isinstance(proof, dict) or Path(path).stat().st_size > MAX_PDF_BYTES:
        return False
    use_context = proof.get('reference_cover_context_used')
    if type(use_context) is not bool:
        return False
    if use_context and (cover_identity_context is None or
            digest(cover_identity_context) != proof.get('reference_cover_context_sha256')):
        return False
    context = cover_identity_context if use_context else None
    with Path(path).open('rb') as handle:
        raw = handle.read(MAX_PDF_BYTES+1)
    if len(raw) > MAX_PDF_BYTES or hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ValueError('Endnote region proof belongs to a different or oversized source PDF')
    with pdfplumber.open(io.BytesIO(raw)) as document:
        if type(page_number) is not int or not 1 <= page_number <= len(document.pages):
            raise ValueError('Endnote source page is outside the original PDF')
        actual = observe_native_endnote_region(document.pages[page_number-1], page_number,
            source_sha256=source_sha256, cover_identity_context=context)
    return actual['status'] == 'observed' and actual == proof
