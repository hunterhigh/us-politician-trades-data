"""Source-bound modern House word/row partition for monochrome native paint.

Raw words, physical rows and their IDs are historical observations. A separate
effective interpretation may supply a complete row only when all original
members are displayed or uniquely explained by a displayed full-word alias.
No source JSON digest, surviving word fragment or absent anchor grants a fact.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re

from .house import HouseIndexError

PARSER_VERSION = 'house-ptr-native-bichrome/v1'
MODE = 'source_pdfminer_bichrome_partition/v1'
OPACITY_PARSER_VERSION = 'house-ptr-native-opacity/v1'
OPACITY_MODE = 'source_pdfminer_opacity_partition/v1'
PARSER_VERSIONS = frozenset({PARSER_VERSION, OPACITY_PARSER_VERSION})

DISPLAY_DOMAIN = 'opaque_white_paper/v1'
MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 32
MAX_ARCHIVED_PAGES = 96
MAX_CHARACTERS = 25_000
MAX_TOTAL_CHARACTERS = 100_000
MAX_ARCHIVED_CHARACTERS = 400_000
MAX_DOCUMENT_GRAPHICS_STATE_EVENTS = 10_000
MAX_DOCUMENT_PAINT_EVENTS = 100_000
MAX_ROW_WORK = 2_000_000
MAX_PROOF_BYTES = 64 * 1024 * 1024
BLOCKER = 'native_bichrome_row_visibility_unknown'


def is_native_contrast(value):
    """Recognize explicit source-bound families without changing old versions."""
    return (value.get('parser_version') if isinstance(value, dict) else value) in PARSER_VERSIONS


class SourceInventoryUnavailable(HouseIndexError):
    """No new source absence/visibility claim is available."""


def _encoded(value):
    content = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    for chunk in encoder.iterencode(value):
        if len(chunk) > MAX_PROOF_BYTES - len(content):
            raise HouseIndexError('House native contrast proof byte budget exceeded')
        raw = chunk.encode('utf-8')
        if len(raw) > MAX_PROOF_BYTES - len(content):
            raise HouseIndexError('House native contrast proof byte budget exceeded')
        content.extend(raw)
    return bytes(content)


def _json(value):
    return json.loads(_encoded(value))


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _load(root, metadata_path):
    root = Path(root).resolve()
    path = Path(metadata_path).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise HouseIndexError('House native contrast metadata is unavailable or outside its source root')
    try:
        metadata = json.loads(path.read_text(encoding='utf-8'))
        relative = metadata['archive_path']
        if (not isinstance(relative, str) or '\\' in relative or Path(relative).is_absolute()
                or '..' in Path(relative).parts):
            raise ValueError('archive path')
        pdf = (root / relative).resolve()
        if root not in pdf.parents or not pdf.is_file() or pdf.stat().st_size > MAX_BYTES:
            raise ValueError('source unavailable or byte budget')
        content = pdf.read_bytes()
        if hashlib.sha256(content).hexdigest() != metadata['sha256']:
            raise ValueError('source hash')
        if (metadata.get('source_id') != 'house_clerk' or metadata.get('filing_type') != 'P'
                or not re.fullmatch(r'[0-9]{1,20}', str(metadata.get('document_id', '')))):
            raise ValueError('source metadata')
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise HouseIndexError('House native contrast source binding is invalid') from exc
    return metadata, pdf, content


def _selected_source(metadata, root, metadata_path, pdf_path, content):
    """The new source proof requires the actual controlled House archive pair."""
    from .house import document_url
    year, doc, sha = metadata.get('filing_year'), str(metadata.get('document_id')), metadata.get('sha256')
    if (type(year) is not int or not 1000 <= year <= 9999 or
            not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or
            metadata.get('source_url') != document_url(year, 'P', doc)):
        raise HouseIndexError('House native contrast controlled archive identity differs')
    relative = f'house_clerk/documents/{year}/{doc}/{sha}.pdf'
    expected = Path(root).resolve()/relative
    if (metadata['archive_path'] != relative or pdf_path != expected.resolve() or
            Path(metadata_path).resolve() != expected.with_suffix('.json').resolve() or
            ('byte_length' in metadata and (type(metadata['byte_length']) is not int or
                                          metadata['byte_length'] != len(content)))):
        raise HouseIndexError('House native contrast controlled archive path or byte length differs')


def _box(value):
    return [value[k] for k in ('x0', 'top', 'x1', 'bottom')]


def _overlap(left, right):
    return max(left[0], right[0]) < min(left[2], right[2]) and max(left[1], right[1]) < min(left[3], right[3])


def _black(char):
    from .native_bichrome_visibility import mono
    return mono(char.get('ncs'), char.get('non_stroking_color')) == 0


def _inventory(pdf, sha, *, include_opacity=False, allow_long=False):
    from .house_native_visibility import _source_characters, _words
    from .house_native_coverage import observe_native_geometry
    from .house_ptr import _modern_date_anchors, _explicit_zero_transaction_declaration
    if not 0 < len(pdf.pages) <= (MAX_ARCHIVED_PAGES if allow_long else MAX_PAGES):
        raise HouseIndexError('House native contrast page budget exceeded')
    character_limit = (MAX_ARCHIVED_CHARACTERS
                       if allow_long and len(pdf.pages) > MAX_PAGES else MAX_TOTAL_CHARACTERS)
    pages, observed, hints, total_characters = [], [], [], 0
    graphics = []
    total_graphics_events = total_paint_events = 0
    for number, page in enumerate(pdf.pages, 1):
        if include_opacity:
            # The explicit observer bounds gs reference resolution before the
            # unchanged historical observer can resolve any used state.
            from .pdf_native_observer import observe_source_characters
            try:
                cc, mm, ff, pp = observe_source_characters(page, max_characters=MAX_CHARACTERS,
                    max_tokens=100_000, include_graphics_state_events=True)
            except Exception as exc:
                raise HouseIndexError('House opacity source inventory is unavailable') from exc
        try:
            chars, modes, flags, paint = _source_characters(page)
        except Exception as exc:
            raise SourceInventoryUnavailable('House native contrast source inventory is unavailable') from exc
        total_characters += len(chars)
        if total_characters > character_limit:
            raise HouseIndexError('House native contrast document character budget exceeded')
        if include_opacity:
            if _json((cc, mm, ff)) != _json((chars, modes, flags)):
                raise HouseIndexError('House opacity original glyph inventory differs')
            total_graphics_events += len(pp['graphics_state_events'])
            total_paint_events += pp['paint_event_count']
            if (total_graphics_events > MAX_DOCUMENT_GRAPHICS_STATE_EVENTS or
                    total_paint_events > MAX_DOCUMENT_PAINT_EVENTS):
                raise HouseIndexError('House opacity document source event budget exceeded')
            graphics.append(pp['graphics_state_events'])
        words = page.extract_words(x_tolerance=2, y_tolerance=2, extra_attrs=['size'])
        pages.append({'width': page.width, 'height': page.height, 'words': words,
            'native_page_geometry': observe_native_geometry(page, sha, number)})
        if _words(chars) != words:
            raise HouseIndexError('House native contrast original word members differ')
        # Hints select a mechanism, never prove a glyph visible. The subsequent
        # closed source profile accounts for actual colors, operators and order.
        hint = any(mode == 0 and not _black(c) for c, mode in zip(chars, modes))
        work = 0
        if not hint:
            for primitive in [*page.rects, *page.curves]:
                color = primitive.get('non_stroking_color')
                values = color if isinstance(color, (list, tuple)) else [color]
                if not primitive.get('fill') or not values or not all(v == values[0] and v in (0, 1) for v in values):
                    continue
                for c, mode in zip(chars, modes):
                    work += 1
                    if work > 200_000:
                        hint = None
                        break
                    if mode == 0 and _overlap(_box(c), _box(primitive)):
                        hint = True
                        break
                if hint is not False:
                    break
        observed.append((chars, modes, flags, paint))
        hints.append(hint)
    text = ' '.join(str(w['text']) for w in pages[0]['words'])
    compact = re.sub('[^A-Z]', '', text.upper())
    large = [str(w['text']).strip() for w in pages[0]['words'] if float(w.get('size', 0)) >= 18]
    # Native financial anchors or a native zero declaration distinguish this
    # route from a pure paper/image/legacy layout; raw headings only select it.
    native_rows = any(_modern_date_anchors(_words([c for c, m in zip(chars, modes) if m == 0]), p['width'])
                      for p, (chars, modes, _, _) in zip(pages, observed))
    result = (pages, observed, hints, text, compact, large, bool(native_rows or _explicit_zero_transaction_declaration(pages)))
    return (*result, graphics) if include_opacity else result


def _selection(metadata, inventory):
    _, _, hints, text, compact, large, native = inventory[:7]
    opacity = len(inventory) > 7 and any(inventory[7])
    modern = (f"#{metadata['document_id']}" in text and
              ('PERIODICTRANSACTIONREPORT' in compact or large[:3] == ['P', 'T', 'R']))
    active = (True if opacity or any(value is True for value in hints) else None if None in hints else False) if modern and native else False
    return {'schema_version': 'house-native-contrast-plan/v1', 'activated': active,
        'status': 'unknown' if active is None else 'observed',
        'parser_version': (OPACITY_PARSER_VERSION if opacity else PARSER_VERSION) if active is not False else None,
        'source_sha256': metadata['sha256'], 'document_id': str(metadata['document_id']),
        'observed_character_count': sum(len(old[0]) for old in inventory[1]),
        'character_budget': (MAX_ARCHIVED_CHARACTERS
            if metadata.get('filing_year') == 2025 and len(inventory[0]) > MAX_PAGES
            else MAX_TOTAL_CHARACTERS),
        'route': 'modern_native' if modern and native else 'existing_paper_image_or_legacy',
        'reasons': ['native_bichrome_selection_budget_exceeded'] if active is None else []}


def plan_house_native_contrast(archive_root, metadata_path):
    """Observe the actual source before a normal success/failure cache skip."""
    import pdfplumber
    metadata, path, content = _load(archive_root, metadata_path)
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            plan = _selection(metadata, _inventory(pdf, metadata['sha256'], include_opacity=True,
                                                   allow_long=metadata.get('filing_year') == 2025))
            if plan['activated'] is not False:
                _selected_source(metadata, archive_root, metadata_path, path, content)
            return plan
    except HouseIndexError:
        raise
    except Exception as exc:
        raise HouseIndexError('House native contrast source plan cannot be observed') from exc


def _word_signature(chars):
    return [(c['text'], c['fontname'], c['size'], c['matrix'], c['adv'], _box(c)) for c in chars]


def _build(metadata, pdf, inventory, *, parser_version=PARSER_VERSION):
    from pdfplumber.utils.text import WordExtractor
    from .native_bichrome_visibility import inspect_bichrome_native_page, POLICY_VERSION
    opacity = parser_version == OPACITY_PARSER_VERSION
    if opacity:
        from .native_opacity_visibility import inspect_opacity_native_page, POLICY_VERSION
    inspect_page = inspect_opacity_native_page if opacity else inspect_bichrome_native_page
    mode = OPACITY_MODE if opacity else MODE
    family = 'opacity' if opacity else 'bichrome'
    grid_conflicts = set()
    from .house_native_visibility import _words
    from .house_ptr import parse_word_pages
    pages, observed, hints, *_ = inventory
    if opacity:
        # Preserve the established native raw-block boundary at repeated table
        # headers. Unknown image/alpha body text is not a continuation header.
        # Only the existing non-filtering unknown observation is reused; all
        # original words remain and the old profile grants no visible field.
        from .house_native_visibility import observe_visibility, unknown
        from .house_native_coverage import _header_cells
        pages = list(pages)
        for index in range(1, len(pages)):
            if _header_cells(pages[index], pages[index]['native_page_geometry']) is not None:
                legacy = observe_visibility(pdf.pages[index], pages[index], metadata['sha256'], index+1)
                if unknown(legacy) and legacy['words'] == pages[index]['words']:
                    pages[index] = legacy
    plan = _selection(metadata, inventory)
    row_members = []
    raw = parse_word_pages(metadata, metadata['sha256'], pages,
                           copy_allowed=getattr(pdf.doc, 'is_extractable', None), _row_member_sink=row_members)
    if 'source_report_ledger' not in raw:
        from .house_ptr import _modern_report_ledger
        raw['source_report_ledger'] = _modern_report_ledger(metadata['sha256'], pages, raw['transactions'])
    if 'source_word_pages' not in raw:
        raw['source_word_pages'] = _json(pages)
        raw['source_word_pages_sha256'] = _digest(pages)
    tuples, members, aliases, source_pages, states = [], [], [], [], []
    for number, (page, old, hint) in enumerate(zip(pdf.pages, observed, hints), 1):
        chars, modes, flags, paint = old
        selected = (opacity and bool(inventory[7][number-1])) or hint is not False or any(mode != 0 for mode in modes) or any(flags)
        proof = inspect_page(page, display_domain=DISPLAY_DOMAIN) if selected else {
            'status': 'unchanged_legacy_native', 'reason': 'mechanism_absent',
            'chars': chars, 'render_modes': modes, 'visibility_flags': flags, 'paint': paint,
            'glyph_states': ['spacing' if c['text'].isspace() else 'displayed' for c in chars]}
        if proof['status'] == 'observed':
            if _json(proof['chars']) != _json(chars) or proof['render_modes'] != modes:
                raise HouseIndexError('House contrast glyph inventory differs from original words')
            if opacity:
                from .pdf_native_observer import observe_source_characters
                cc, mm, ff, pp = observe_source_characters(page, max_characters=MAX_CHARACTERS,
                    max_tokens=100_000, include_path_events=True, include_graphics_state_events=True)
                if _json((proof['chars'], proof['render_modes'], proof['visibility_flags'], proof['paint'])) != _json((cc, mm, ff, pp)):
                    raise HouseIndexError('House opacity full source paint projection differs')
                paint = pp
            ss = proof['glyph_states']
            # Preserve the original physical geometry. A zero-alpha stroke
            # cannot disappear and merge source blocks or grant their grid.
            if opacity and proof.get('displayed_stroke_indices') != list(range(len(page.lines))):
                grid_conflicts.add(number)
        elif proof['status'] == 'unchanged_legacy_native':
            ss = proof['glyph_states']
        else:
            ss = ['unknown'] * len(chars)
        states.append(ss)
        extracted = list(WordExtractor(x_tolerance=2, y_tolerance=2, extra_attrs=['size']).iter_extract_tuples(chars))
        if [word for word, _ in extracted] != pages[number-1]['words']:
            raise HouseIndexError('House contrast exact source word correspondence failed')
        ids = {id(c): index for index, c in enumerate(chars)}
        index = [[ids[id(c)] for c in cs] for _, cs in extracted]
        # Full source word equivalence explains a superseded duplicate, not a
        # partial hidden word. Unknown or partly displayed old words never alias.
        a = {}
        signatures = {}
        for wi, (_, cs) in enumerate(extracted):
            if all(ss[ci] in {'displayed', 'spacing'} for ci in index[wi]):
                signatures.setdefault(_digest(_word_signature(cs)), []).append(wi)
        for wi, (_, cs) in enumerate(extracted):
            if index[wi] and all(ss[ci] in {'no_effect', 'occluded'} for ci in index[wi]):
                counterparts = signatures.get(_digest(_word_signature(cs)), [])
                if len(counterparts) == 1:
                    a[wi] = counterparts[0]
        tuples.append(extracted); members.append(index); aliases.append(a)
        source_pages.append({'page_number': number, 'proof': _json(proof),
            'raw_characters': _json(chars), 'raw_render_modes': modes,
            'raw_visibility_flags': flags, 'raw_paint': _json(paint),
            'word_character_indices': index, 'word_aliases': {str(k): v for k, v in a.items()}})
    if len(raw['transactions']) * sum(len(old[0]) for old in observed) > MAX_ROW_WORK:
        raise HouseIndexError('House contrast row interpretation budget exceeded')
    if [item['raw_row_id'] for item in row_members] != [r['extraction_id'] for r in raw['transactions']]:
        raise HouseIndexError('House exact raw row membership is incomplete')
    row_sets = [[set(next((b['word_indices'] for b in item['blocks'] if b['page_number'] == p+1), []))
                 for p in range(len(pages))] for item in row_members]
    field_sets = [[set(next((b['field_word_indices'] for b in item['blocks'] if b['page_number'] == p+1), []))
                   for p in range(len(pages))] for item in row_members]
    all_members = [set().union(*(r[p] for r in row_sets)) for p in range(len(pages))]
    conflicts = {ri for p, words in enumerate(tuples) for wi in range(len(words))
                 for owners in [[ri for ri, sets in enumerate(row_sets) if wi in sets[p]]]
                 if len(owners) > 1 for ri in owners}
    dispositions, runtime = [], []
    for ri, (row, sets) in enumerate(zip(raw['transactions'], row_sets)):
        reasons = (['native_opacity_grid_visibility_unresolved'] if any(sets[p] and p+1 in grid_conflicts for p in range(len(pages))) else [])
        if opacity and 'native_text_visibility_unknown' in row.get('field_problems', []):
            reasons.append('native_text_visibility_unknown')
        risks, raw_members, effective = [], [], []
        for p, indices in enumerate(sets):
            for wi in sorted(indices):
                raw_members.append({'page_number': p+1, 'word_index': wi,
                                    'source_character_indices': members[p][wi]})
                alias_in_field = aliases[p].get(wi) in field_sets[ri][p]
                if (wi in field_sets[ri][p] and any(states[p][ci] not in {'displayed', 'spacing'} for ci in members[p][wi])
                        and not alias_in_field):
                    risks.append({'page_number': p+1, 'word_index': wi,
                        'source_character_indices': members[p][wi],
                        'states': [states[p][ci] for ci in members[p][wi]]})
            # Only original row members plus original pre-body context enter
            # this interpretation. A missing next anchor cannot merge rows.
            first = min((s['bbox_points'][1] for r in raw['transactions']
                         for s in r['evidence']['segments'] if s['page'] == p+1), default=pages[p]['height'])
            take = {id(c) for wi, (w, cs) in enumerate(tuples[p])
                if wi in indices or (wi not in all_members[p] and float(w['top']) < first)
                for c in cs}
            cs = [c for ci, c in enumerate(observed[p][0]) if id(c) in take and states[p][ci] in {'displayed', 'spacing'}]
            effective_page = {**pages[p], 'words': _words(cs)}
            if opacity:
                # The new per-show source proof replaces conservative old
                # alpha flags for interpretation, never for raw membership.
                effective_page.pop('native_visibility_observation', None)
            effective.append(effective_page)
        if risks:
            reasons.append(BLOCKER)
        if ri in conflicts:
            reasons.append('native_bichrome_raw_row_members_not_unique')
        one = None
        try:
            one = parse_word_pages(metadata, metadata['sha256'], effective,
                                   copy_allowed=getattr(pdf.doc, 'is_extractable', None))
            ers = one['transactions']
        except HouseIndexError:
            ers = []
        unique = (len(ers) == 1 and ers[0]['evidence']['page'] == row['evidence']['page'] and
                  ers[0]['evidence']['bbox_points'][1] == row['evidence']['bbox_points'][1])
        if not unique:
            reasons.append('native_bichrome_effective_row_correspondence_unresolved')
        projection = deepcopy(ers[0]) if unique else None
        if projection is not None:
            projection['extraction_id'] = row['extraction_id']
            projection['evidence'] = deepcopy(row['evidence'])
        # Original row and ledger remain even when no effective anchor exists.
        dispositions.append({'raw_row_id': row['extraction_id'],
            'status': 'unknown' if reasons else 'current_projected',
            'reasons': sorted(set(reasons)), 'raw_word_members': raw_members,
            'unavailable_word_members': risks,
            'slot_refs': deepcopy(raw['source_report_ledger']['logical_rows'][ri]['slot_refs']),
            'current_projected': projection if not reasons else None})
        runtime.append(one if not reasons else None)
    # A source role is required even if its raw text selected this route. Each
    # row interpretation independently reads the displayed original header.
    observation = {'schema_version': f'house-native-{family}-observation/v1',
        'source_sha256': metadata['sha256'], 'policy_version': POLICY_VERSION,
        'display_domain': DISPLAY_DOMAIN, 'pages': source_pages, 'plan': plan}
    binding = {'schema_version': f'house-native-{family}-binding/v1',
        'source_sha256': metadata['sha256'], 'document_id': str(metadata['document_id']),
        'policy_version': POLICY_VERSION, 'parser_version': parser_version,
        'display_domain': DISPLAY_DOMAIN, 'observation_sha256': _digest(observation),
        'metadata_sha256': _digest(metadata), 'coverage_complete': False}
    report = {**deepcopy(raw), 'parser_version': parser_version, 'native_contrast_mode': mode,
        'native_contrast_observation': observation, 'native_contrast_binding': binding,
        'native_contrast_dispositions': dispositions, 'source_status': 'partial', 'coverage_complete': False}
    from .source_row_ledger import page_ledger, report_ledger
    ledger = report['source_report_ledger']
    for number, source_page in enumerate(source_pages, 1):
        if source_page['proof']['status'] != 'unchanged_legacy_native':
            page = ledger['pages'][number-1]
            ledger['pages'][number-1] = page_ledger(**{key: page[key] for key in (
                'source_id', 'document_sha256', 'page_number', 'adapter_version', 'slots',
                'unresolved_regions', 'coordinate_frame')}, coverage='unknown', coverage_reason=BLOCKER)
    report['source_report_ledger'] = report_ledger(source_id='house_clerk', document_sha256=metadata['sha256'],
        adapter_version=ledger['adapter_version'], page_count=len(pages), pages=ledger['pages'],
        logical_rows=[{k: v for k, v in r.items() if k != 'qualification'} for r in ledger['logical_rows']],
        unresolved_data_slots=ledger['unresolved_data_slots'])
    return _json(report), (raw, runtime)


def parse_selected_native_contrast(archive_root, metadata_path, *, required=False):
    """Return None for an absent mechanism; active unknown never falls back."""
    import pdfplumber
    metadata, path, content = _load(archive_root, metadata_path)
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            inventory = _inventory(pdf, metadata['sha256'], include_opacity=True,
                                   allow_long=metadata.get('filing_year') == 2025)
            plan = _selection(metadata, inventory)
            if plan['activated'] is False:
                if required:
                    raise HouseIndexError('House native contrast source no longer selects this parser')
                return None
            _selected_source(metadata, archive_root, metadata_path, path, content)
            report, _ = _build(metadata, pdf, inventory, parser_version=plan['parser_version'])
            return report
    except HouseIndexError:
        raise
    except Exception as exc:
        raise HouseIndexError('House native contrast source interpretation failed') from exc


def _replay(extraction, evidence_root):
    if evidence_root is None:
        raise HouseIndexError('House native contrast requires an evidence root')
    version = extraction.get('parser_version')
    expected_mode = OPACITY_MODE if version == OPACITY_PARSER_VERSION else MODE
    if version not in PARSER_VERSIONS or extraction.get('native_contrast_mode') != expected_mode:
        raise HouseIndexError('House native contrast parser binding differs')
    source = extraction.get('source', {})
    relative = source.get('archive_path')
    if not isinstance(relative, str) or '\\' in relative or '..' in Path(relative).parts or Path(relative).is_absolute():
        raise HouseIndexError('House native contrast replay source path is invalid')
    metadata_path = Path(evidence_root) / Path(relative).with_suffix('.json')
    import pdfplumber
    metadata, path, content = _load(evidence_root, metadata_path)
    _selected_source(metadata, evidence_root, metadata_path, path, content)
    if metadata['sha256'] != extraction.get('source_sha256'):
        raise HouseIndexError('House native contrast replay source hash differs')
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            inventory = _inventory(pdf, metadata['sha256'], include_opacity=version == OPACITY_PARSER_VERSION,
                                   allow_long=metadata.get('filing_year') == 2025)
            if _selection(metadata, inventory)['activated'] is False:
                raise HouseIndexError('House native contrast source no longer selects this parser')
            fresh, runtime = _build(metadata, pdf, inventory, parser_version=version)
    except HouseIndexError:
        raise
    except Exception as exc:
        raise HouseIndexError('House native contrast original source cannot be replayed') from exc
    if fresh != extraction:
        raise HouseIndexError('House native contrast saved projection differs from original source')
    return fresh, runtime


def replay_native_contrast(extraction, evidence_root):
    """Rebuild all words, paint, original rows and effective rows from source."""
    return _replay(extraction, evidence_root)[0]


def bounded_legacy_unknown(archive_root, metadata_path):
    """Preserve an existing all-unknown v1 reading, never a native fallback fact.

    The independent bounded observer must succeed before legacy word/geometry
    materialization. This handles an unavailable legacy wrapper, not an actual
    unbounded source. The normal source planner still reports unavailable.
    """
    import pdfplumber
    from .pdf_native_observer import observe_source_characters
    from .house_ptr import observe_native_word_pages, parse_word_pages
    from .house_native_visibility import unknown
    metadata, _, content = _load(archive_root, metadata_path)
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if not 0 < len(pdf.pages) <= (MAX_ARCHIVED_PAGES if metadata.get('filing_year') == 2025 else MAX_PAGES):
                raise ValueError('page budget')
            total = 0
            for page in pdf.pages:
                chars, _, _, _ = observe_source_characters(page, max_characters=MAX_CHARACTERS, max_tokens=100_000)
                total += len(chars)
                if total > (MAX_ARCHIVED_CHARACTERS if metadata.get('filing_year') == 2025 else MAX_TOTAL_CHARACTERS):
                    raise ValueError('document character budget')
            pages = observe_native_word_pages(pdf, metadata['sha256'])
            if not pages or not all(unknown(p) for p in pages):
                raise ValueError('no complete legacy unknown observation')
            report = parse_word_pages(metadata, metadata['sha256'], pages,
                                      copy_allowed=getattr(pdf.doc, 'is_extractable', None))
            if any(BLOCKER not in r.get('field_problems', []) and
                   'native_text_visibility_unknown' not in r.get('field_problems', [])
                   for r in report['transactions']) or report.get('document_disposition') is not None:
                raise ValueError('legacy unknown grants facts or zero')
            return report
    except Exception as exc:
        raise HouseIndexError('House source cannot retain a bounded all-unknown native observation') from exc


def qualify_native_contrast(extraction, identity, *, evidence_root, revision_ids=frozenset()):
    from .house_ptr import qualify_automatic, QUALIFICATION_SCHEMA
    fresh, (raw, reports) = _replay(extraction, evidence_root)
    metadata_path = Path(evidence_root) / Path(extraction['source']['archive_path']).with_suffix('.json')
    current = plan_house_native_contrast(evidence_root, metadata_path)
    if current['parser_version'] != extraction['parser_version']:
        raise HouseIndexError('House native contrast current source selects a different parser')
    good, bad, advisories, dispositions, revisions = [], [], [], [], []
    for original, disposition, report in zip(raw['transactions'], fresh['native_contrast_dispositions'], reports):
        reasons = list(disposition['reasons'])
        fact = None
        if report is not None:
            effective_id = report['transactions'][0]['extraction_id']
            q = qualify_automatic(report, identity,
                _source_bound_revision_ids=frozenset({effective_id}) if original['extraction_id'] in revision_ids else frozenset())
            if len(q['transactions']) == 1:
                fact = deepcopy(q['transactions'][0]); fact['id'] = original['extraction_id']
            else:
                reasons.extend(reason for r in q['quarantined'] for reason in r.get('reasons', []))
            for item in q.get('advisories', []):
                advisories.append({**item, 'extraction_id': original['extraction_id']})
            revisions.extend({**item, 'extraction_id': original['extraction_id']}
                             for item in q.get('revision_observations', []))
        if fact is None:
            bad.append({'extraction_id': original['extraction_id'],
                'reasons': sorted(set(reasons or [BLOCKER])), 'source_sha256': raw['source_sha256'],
                'evidence': deepcopy(original['evidence']), 'raw_cells': deepcopy(original['raw_cells']),
                'slot_refs': deepcopy(disposition['slot_refs'])})
        else:
            good.append(fact)
        dispositions.append({**deepcopy(disposition),
            'status': 'current_projected' if fact is not None else 'unknown',
            'reasons': sorted(set(reasons)), 'current_projected': fact})
    # This bounded local partition never certifies whole-report zero/completion.
    return {'schema_version': QUALIFICATION_SCHEMA, 'source_sha256': raw['source_sha256'],
        'parser_version': fresh['parser_version'], 'document_id': raw['source']['document_id'],
        'identity': deepcopy(identity), 'document_disposition': None,
        'source_report_ledger': deepcopy(fresh['source_report_ledger']),
        'transactions': good, 'quarantined': bad, 'advisories': advisories,
        'native_contrast_mode': fresh['native_contrast_mode'], 'native_contrast_binding': deepcopy(fresh['native_contrast_binding']),
        'native_contrast_dispositions': dispositions, 'source_status': 'partial', 'coverage_complete': False,
        **({'revision_observations': revisions} if revisions else {}),
        'qualification': {'method': 'deterministic_automatic_rules',
            'status': 'qualified_rows' if good else 'quarantined_rows',
            'qualified_count': len(good), 'quarantined_count': len(bad),
            'advisory_count': len(advisories), 'zero_transaction_document_count': 0,
            'document_reasons': [], 'production_eligible': bool(good)}}
