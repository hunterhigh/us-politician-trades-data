"""Source-object proof for one fixed native OGE privacy-statement layout.

This is not a title classifier. Unknown drawings, text styles and raster
objects cannot be excused by a Privacy/Endnotes heading. No OCR is invoked.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

RULE = "oge-278t-native-privacy-role/v1"
# Native 2019 form layout observed in controlled official PDF
# 62e52bc99400ec00714ee6ccf57f3c2b9ce9da93a90890ae562a75448fe0fcc5,
# page 7. The fingerprint contains every body glyph and its PDF geometry;
# report identity and page number never select this role.
BODY_LAYOUT_SHA256 = "a945bba71130936fad550b60f29021e516b37228862d0b6f93fe4fcc11c31ce5"
LINE_LAYOUT_SHA256 = "cb37e40a51c5c6b6ca797ba914d38ef6fbdfcd21190b8871f0fd1186d0efbb1f"
_OPS = {"BT","ET","Q","RG","rg","g","G","S","TJ","Td","Tm","Tf","Tj","cm","l","m","q","w"}


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(",", ":"),allow_nan=False).encode()).hexdigest()


def _char_geometry(chars):
    return sorted([[c['text'],*[round(float(c[key]),5) for key in ('x0','top','x1','bottom')]]
                   for c in chars],key=lambda item:(item[2],item[1],item[0]))


def _line_geometry(lines):
    return sorted([[*[round(float(line[key]),5) for key in ('x0','top','x1','bottom','linewidth')],
                    list(line['stroking_color']) if isinstance(line['stroking_color'],(list,tuple))
                      else [line['stroking_color']],line['stroke'],line['fill']]
                   for line in lines])


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
    # Tests/adapters with partial page interfaces are not source-object inventories.
    if not all(hasattr(page,key) for key in ('chars','objects','annots','page_obj','bbox')):
        return unknown('native_page_object_inventory_unavailable')
    if (tuple(float(v)for v in page.bbox)!=(0.,0.,842.,595.) or page.rotation not in (0,90) or
            any(key not in {'char','line'} and values for key,values in page.objects.items()) or
            page.annots):
        return unknown('unsupported_page_geometry_or_nontext_objects')
    chars=page.chars
    if not chars or len(chars)>10_000:
        return unknown('native_character_inventory_unsupported')
    for c in chars:
        if (not isinstance(c.get('text'),str) or len(c['text'])!=1 or c.get('fontname')!='Helvetica' or
                c.get('upright') is not True or abs(float(c.get('size',0))-10)>1e-5 or
                c.get('non_stroking_color') not in (0,(0,),[0]) or
                list(c.get('matrix',()))[:4]!=[1.,0.,0.,1.] or
                any(not math.isfinite(float(c.get(k,float('nan')))) for k in ('x0','top','x1','bottom')) or
                not 0<=c['x0']<c['x1']<=page.width or not 0<=c['top']<c['bottom']<=page.height):
            return unknown('native_character_style_or_geometry_unsupported')
    # Only the standard Type1 Helvetica program is admitted; Type3 glyphs,
    # remapping, transparency, clipping and invisible text are outside scope.
    from pdfminer.pdftypes import resolve1
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.psparser import PSEOF,PSKeyword
    fonts=resolve1(page.page_obj.resources.get('Font',{}))
    if not fonts:
        return unknown('standard_font_program_unavailable')
    for value in fonts.values():
        value=resolve1(value)
        if (set(value)!={'Subtype','Type','BaseFont','Encoding'} or
                getattr(value['Subtype'],'name',None)!='Type1' or
                getattr(value['Type'],'name',None)!='Font' or
                getattr(value['BaseFont'],'name',None)!='Helvetica' or
                getattr(value['Encoding'],'name',None)!='WinAnsiEncoding'):
            return unknown('standard_font_program_unavailable')
    parser=PDFContentParser(page.page_obj.contents);operators=[];tokens=0;depth=0
    operands=[];text_runs=[];text_run=bytearray()
    try:
        while True:
            _,obj=parser.nextobject();tokens+=1
            if tokens>100_000:return unknown('native_content_budget_exceeded')
            if isinstance(obj,PSKeyword):
                op=obj.name.decode('ascii')
                if op not in _OPS:return unknown('unsupported_native_content_operator')
                operators.append(op)
                if op in {'Tj','TJ'}:
                    if len(operands)!=1 or op=='Tj' and not isinstance(operands[0],bytes):
                        return unknown('unsupported_native_text_show')
                    sequence=[operands[0]] if op=='Tj' else operands[0]
                    if (not isinstance(sequence,list) or any(not isinstance(value,bytes) and
                            (type(value)not in (int,float) or not math.isfinite(value)) for value in sequence)):
                        return unknown('unsupported_native_text_show')
                    for value in sequence:
                        if isinstance(value,bytes):text_run.extend(value)
                elif text_run:
                    text_runs.append(bytes(text_run));text_run.clear()
                operands=[]
                depth+=(op=='q')-(op=='Q')
                if not 0<=depth<=32:return unknown('unsupported_graphics_state')
            else:
                operands.append(obj)
    except PSEOF:
        pass
    if text_run:text_runs.append(bytes(text_run))
    if depth!=0:return unknown('unsupported_graphics_state')
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
