"""Explicit source-selected rectangular-clipping display policy for native table rows.

Historical mode-zero/three observations retain their original meaning. This
policy additionally proves source-painted contrast, binary alpha and rectangular clipping on an opaque white display
domain, before deriving any financial field or form role.
"""
from collections import Counter
from io import BytesIO
import json

from .native_source_visibility import _black
from .native_text_partition import (_source, _safe, _observe, MAX_PAGES,
    MAX_TOTAL_CHARACTERS, MAX_PROOF_BYTES, SCHEMA)
from .pdf_native_observer import observe_source_characters

PLAN_SCHEMA = 'native-clip-partition-plan/v1'
POLICY_VERSION = 'source-native-clip-opaque-white/v1'
DISPLAY_DOMAIN = 'opaque_white_paper/v1'
MAX_TOTAL_GRAPHICS_STATE_EVENTS = 10_000
MAX_TOTAL_PAINT_EVENTS = 100_000
MAX_TOTAL_CLIPPING_EVENTS = 10_000
MAX_TOTAL_CLIPPING_PATH_POINTS = 100_000


def _plan(raw, sha, document):
    pages, inventories, total, active = [], [], 0, False
    total_graphics_states = total_paint = 0
    total_clips = total_clip_points = 0
    reasons = []
    if not 0 < len(document.pages) <= MAX_PAGES:
        reasons = ['native_clip_page_budget_exceeded']
    else:
        for number, page in enumerate(document.pages, 1):
            try:
                observed = observe_source_characters(page, include_path_events=True, include_graphics_state_events=True, include_clipping_events=True)
                chars, modes, flags, paint = observed
                total += len(chars)
                if total > MAX_TOTAL_CHARACTERS:
                    raise ValueError('native_clip_total_character_budget_exceeded')
                total_graphics_states += len(paint['graphics_state_events'])
                total_paint += paint['paint_event_count']
                total_clips += len(paint['clipping_events'])
                total_clip_points += sum(max(1,(len(component)-1)//2)
                    for event in paint['clipping_events'] for component in event['raw_path'])
                if (total_graphics_states > MAX_TOTAL_GRAPHICS_STATE_EVENTS or
                        total_paint > MAX_TOTAL_PAINT_EVENTS or
                        total_clips > MAX_TOTAL_CLIPPING_EVENTS or
                        total_clip_points > MAX_TOTAL_CLIPPING_PATH_POINTS):
                    raise ValueError('native_clip_total_event_budget_exceeded')
                used_graphics_state = bool(paint['graphics_state_events'])
                used_clipping = (bool(paint['clipping_events']) or
                    any(type(mode) is int and 4 <= mode <= 7 for mode in modes))
                # Actual clip requests (even uncommitted or unsupported) select
                # this policy; every other page keeps its existing GS/contrast
                # and hidden-state risks. Unused resources alone select none.
                affected = (used_clipping or used_graphics_state or any(flags) or any(mode != 0 for mode in modes) or
                    any(not _black(char.get('ncs'), char.get('non_stroking_color')) for char in chars) or
                    any(event['fill'] for event in paint['path_events']))
                active = active or used_clipping
                pages.append({'page_number': number, 'status':'observed', 'affected':affected,
                    'used_graphics_state':used_graphics_state,
                    'used_clipping':used_clipping,
                    'character_count':len(chars),
                    'render_mode_counts':dict(sorted(Counter(map(str, modes)).items()))})
                inventories.append(observed)
            except Exception as error:
                reason = str(error)
                if not reason.startswith(('native_', 'source_visibility_')):
                    reason = 'native_clip_inventory_unavailable'
                pages.append({'page_number':number, 'status':'unknown', 'affected':None, 'reason':reason})
                reasons.append(reason)
                break
    plan = {'schema_version':PLAN_SCHEMA, 'policy_version':POLICY_VERSION,
        'display_domain':DISPLAY_DOMAIN, 'source_sha256':sha, 'source_byte_length':len(raw),
        'status':'unknown' if reasons else 'observed', 'activated':active if not reasons or active else None,
        'inventory_complete':not reasons, 'page_count':len(document.pages), 'pages':pages, 'reasons':reasons,
        'graphics_state_event_count':total_graphics_states, 'paint_event_count':total_paint,
        'clipping_event_count':total_clips, 'clipping_path_point_count':total_clip_points}
    return plan, inventories


def _unavailable(raw, sha):
    return {'schema_version':PLAN_SCHEMA, 'policy_version':POLICY_VERSION,
        'display_domain':DISPLAY_DOMAIN, 'source_sha256':sha, 'source_byte_length':len(raw),
        'status':'unknown', 'activated':None, 'inventory_complete':False,
        'page_count':None, 'pages':[], 'reasons':['native_clip_document_unavailable']}


def plan_native_clip_partition(path, expected_sha256):
    raw = _source(path, expected_sha256)
    import pdfplumber
    try:
        with pdfplumber.open(BytesIO(raw)) as document:
            return _safe(_plan(raw, expected_sha256, document)[0])
    except Exception:
        return _unavailable(raw, expected_sha256)


def observe_native_clip_partition(path, expected_sha256):
    from .native_clip_visibility import inspect_rectangular_clip_native_page
    raw = _source(path, expected_sha256)
    import pdfplumber
    try:
        with pdfplumber.open(BytesIO(raw)) as document:
            plan, inventories = _plan(raw, expected_sha256, document)
            profiles = {}
            if plan['inventory_complete']:
                for number, page in enumerate(document.pages, 1):
                    if plan['pages'][number-1]['affected']:
                        profile = inspect_rectangular_clip_native_page(page, display_domain=DISPLAY_DOMAIN)
                        if profile.get('status') == 'observed':
                            observed = inventories[number-1]
                            if any(profile[key] != value for key, value in zip(
                                    ('chars','render_modes','visibility_flags','paint'),observed)):
                                raise ValueError('native_clip_source_inventory_differs')
                        profiles[number] = profile
            result = _safe(_observe(raw, expected_sha256, document,
                source_partition={'plan':plan, 'inventories':inventories, 'profiles':profiles}))
        used = 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(result):
            if len(chunk) > MAX_PROOF_BYTES - used:
                raise ValueError('native_clip_proof_byte_budget_exceeded')
            used += len(chunk.encode('utf-8'))
            if used > MAX_PROOF_BYTES:
                raise ValueError('native_clip_proof_byte_budget_exceeded')
        return result
    except Exception as error:
        reason = str(error)
        if not reason.startswith(('native_', 'source_visibility_')):
            reason = 'native_clip_document_unavailable'
        return {'schema_version':SCHEMA, 'policy_version':POLICY_VERSION, 'source_sha256':expected_sha256,
            'source_byte_length':len(raw), 'activated':None, 'inventory_complete':False, 'status':'unknown',
            'document_reasons':[reason], 'pages':[], 'rows':[], 'inventory':[], 'raw_inventory':[],
            'scope_barriers_before':{}, 'row_reasons':{}, 'cover_unmatched_hidden_regions':[], 'unknown_regions':[]}
