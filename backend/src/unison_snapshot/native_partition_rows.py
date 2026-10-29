"""Interpret a source-replayed visible inventory without changing parser policy."""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json

from .oge import OgeCatalogError


def partition_binding(proof, parser_version):
    """Compact audit reference; its consumer must replay the full source."""
    return {'schema_version': 'native-source-partition-binding/v1',
        'parser_version': parser_version, 'source_sha256': proof['source_sha256'],
        'policy_version': proof['policy_version'],
        'projection_sha256': hashlib.sha256(json.dumps(proof, ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
        'coverage_complete': False,
        'unknown_regions': proof.get('unknown_regions', [])}


def interpret_partition_rows(proof, *, source_sha256, generic, default_owner='Self'):
    """Keep physical positions and attach source uncertainty to their dependents.

    This pure interpreter grants no source authenticity. Its callers must first
    reconstruct the supplied projection from the original archived PDF.
    """
    from .oge_reports import (classify_table_structure, parse_generic_table_rows,
                              parse_table_rows, _compact)

    if proof.get('source_sha256') != source_sha256:
        raise OgeCatalogError('Native partition row source differs')
    inventory = [(page, [_compact(cell) for cell in cells])
                 for page, cells in proof['inventory']]
    barriers = {int(key): value for key, value in proof.get('scope_barriers_before', {}).items()}
    risks = {int(key): list(value) for key, value in proof.get('row_reasons', {}).items()}
    if any(not 0 <= index < len(inventory) for index in set(barriers) | set(risks)):
        raise OgeCatalogError('Native partition constraint is outside its source inventory')
    unresolved = False
    for index, (_, cells) in enumerate(inventory):
        if barriers.get(index):
            unresolved = True
        structure = classify_table_structure(cells)
        if structure and structure['kind'] in {'owner_heading', 'owner_account_heading'}:
            unresolved = False
        if unresolved:
            risks.setdefault(index, []).append('native_hidden_declaration_association_unresolved')
    if generic:
        good, bad, structural = parse_generic_table_rows(inventory,
            source_sha256=source_sha256, scope_barriers_before=barriers)
    else:
        good, bad = parse_table_rows(inventory, source_sha=source_sha256,
                                    default_owner=default_owner)
        structural = []
        locations = defaultdict(deque)
        for index, (page, cells) in enumerate(inventory):
            locations[(page, tuple(cells))].append(index)
        # Both parser output lists retain input order. A repeated identical
        # physical row cannot gain a different position merely by disposition.
        original = sorted(good + bad, key=lambda row: (
            row.get('page_number', 0),
            locations[(row.get('page_number'), tuple(row.get('cells', [])))][0]
            if locations[(row.get('page_number'), tuple(row.get('cells', [])))] else len(inventory),
            'row_number_duplicated' in row.get('reasons', [])))
        for row in original:
            candidates = locations[(row.get('page_number'), tuple(row.get('cells', [])))]
            if not candidates:
                raise OgeCatalogError('Native visible parser row has no physical source position')
            row['source_rows_index'] = candidates.popleft()
    kept, rejected, consumed = [], [], set()
    for row in good + bad:
        index = row['source_rows_index']
        consumed.add(index)
        reasons = sorted(set(row.get('reasons', []) + risks.get(index, [])))
        (rejected if reasons else kept).append({**row, **({'reasons': reasons} if reasons else {})})
    for index, reasons in sorted(risks.items()):
        if not reasons or index in consumed:
            continue
        page, cells = inventory[index]
        # A fully hidden row can become visually blank or structural. Retain
        # its physical unknown without copying hidden values into fact fields.
        stable = f'native-unresolved|{source_sha256}|{index}'
        rejected.append({'extraction_id': 'oge-278t:' + hashlib.sha256(stable.encode()).hexdigest()[:24],
            'page_number': page, 'cells': cells, 'source_rows_index': index,
            'native_unknown_source_row': True, 'reasons': sorted(set(reasons))})
    return kept, rejected, structural
