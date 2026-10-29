"""Source-object proof and replay for fixed native government page profiles.

This is not a title classifier. Unknown drawings, text styles and raster
objects cannot be excused by a Privacy/Endnotes heading. No OCR is invoked.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

RULE = "oge-278t-native-privacy-role/v1"
# Native 2019 form layout observed in controlled official PDF
# 62e52bc99400ec00714ee6ccf57f3c2b9ce9da93a90890ae562a75448fe0fcc5,
# page 7. The fingerprint contains every body glyph and its PDF geometry;
# report identity and page number never select this role.
BODY_LAYOUT_SHA256 = "a945bba71130936fad550b60f29021e516b37228862d0b6f93fe4fcc11c31ce5"
LINE_LAYOUT_SHA256 = "cb37e40a51c5c6b6ca797ba914d38ef6fbdfcd21190b8871f0fd1186d0efbb1f"
from .oge_278t_native_objects import (
    OPS as _OPS, digest as _digest, char_geometry as _char_geometry,
    line_geometry as _line_geometry, inspect_strict_native_objects)


def _bounded_footer(footer, page_number, text_runs):
    """Admit the observed right-aligned, unspaced native footer only.

    The official source writes its name and page number in adjacent Tj calls.
    These are one continuous text run: no positioning or state operator occurs
    between them. A separate run cannot become part of a name by sorting x.
    """
    ordered=sorted(footer,key=lambda c:c['x0'])
    text=''.join(c['text']for c in ordered)
    if len(text)>112 or not re.fullmatch(r"[A-Z][A-Z .'-]*, [A-Z][A-Z .'-]* - Page "+str(page_number),text):
        return None
    if (any(round(c['top'],5)!=551.07 or round(c['bottom'],5)!=561.07 for c in ordered) or
            round(ordered[-1]['x1'],5)!=806 or
            any(round(left['x1'],5)!=round(right['x0'],5) for left,right in zip(ordered,ordered[1:])) or
            text_runs.count(text.encode('ascii'))!=1):
        return None
    return text


def observe_native_privacy_role(page, page_number: int, *, source_sha256: str | None) -> dict:
    base={'rule_version':RULE,'source_sha256':source_sha256,'page_number':page_number,
          'status':'unknown','role':None,'financial_fact_qualification':False}
    def unknown(reason):return {**base,'reason':reason}
    if not isinstance(source_sha256,str) or not re.fullmatch(r'[0-9a-f]{64}',source_sha256):
        return unknown('page_role_source_binding_unavailable')
    inventory = inspect_strict_native_objects(page)
    if inventory['status'] != 'observed':
        return unknown(inventory['reason'])
    chars = inventory['chars']
    text_runs = inventory['text_runs']
    operators = inventory['operators']
    body=[c for c in chars if c['bottom']<=page.height-100]
    footer=[c for c in chars if c['top']>=page.height-60 and c['bottom']<=page.height-25]
    if len(body)+len(footer)!=len(chars):return unknown('unaccounted_native_characters')
    footer_text=_bounded_footer(footer,page_number,text_runs)
    if footer_text is None:
        return unknown('administrative_footer_not_bounded')
    body_hash=_digest(_char_geometry(body));line_hash=_digest(_line_geometry(page.lines))
    if body_hash!=BODY_LAYOUT_SHA256 or line_hash!=LINE_LAYOUT_SHA256:
        return unknown('page_body_or_drawings_differ_from_fixed_privacy_layout')
    return {**base,'status':'non_table','role':'fixed_native_privacy_statement_2019',
            'reason':None,'body_layout_sha256':body_hash,'line_layout_sha256':line_hash,
            'native_character_count':len(chars),'body_character_count':len(body),
            'footer_text':footer_text,'footer_layout_sha256':_digest(_char_geometry(footer)),
            'footer_source_text_run_count':1,'content_operator_sha256':_digest(operators),
            'native_objects_fully_accounted':True,'recognizer_started':False}


def replay_native_privacy_role(path: Path, proof: dict, *, source_sha256: str, page_number: int) -> bool:
    import pdfplumber
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=source_sha256:
        raise ValueError('non-table page proof belongs to a different source PDF')
    with pdfplumber.open(path)as document:
        if type(page_number)is not int or not 1<=page_number<=len(document.pages):
            raise ValueError('non-table proof page is outside source PDF')
        actual=observe_native_privacy_role(document.pages[page_number-1],page_number,source_sha256=source_sha256)
    return actual['status']=='non_table' and actual==proof


def observe_native_page_role(page, page_number: int, *, source_sha256: str | None,
                             cover_identity_context=None) -> dict:
    """Try only known complete source profiles; a title is never authority."""
    privacy = observe_native_privacy_role(page, page_number, source_sha256=source_sha256)
    if privacy['status'] == 'non_table' or page_number != 1:
        return privacy
    from .oge_278t_cover_role import observe_native_cover_role
    return observe_native_cover_role(page, page_number, source_sha256=source_sha256,
                                     cover_identity_context=cover_identity_context)


def replay_native_page_role(path: Path, proof: dict, *, source_sha256: str,
                            page_number: int, cover_identity_context=None) -> bool:
    if not isinstance(proof, dict):
        return False
    if proof.get('rule_version') == RULE:
        return replay_native_privacy_role(path, proof, source_sha256=source_sha256,
                                          page_number=page_number)
    from .oge_278t_cover_role import COVER_RULE, replay_native_cover_role
    if proof.get('rule_version') == COVER_RULE:
        return replay_native_cover_role(path, proof, source_sha256=source_sha256,
            page_number=page_number, cover_identity_context=cover_identity_context)
    return False
