"""Fixed public form wording is source evidence, never an arbitrary page label.

The fixture is only the static 2019 government Privacy Act boilerplate. It
contains no filing facts. Its native layout reproduces the actual controlled
source profile documented in oge_278t_page_roles; mutations test exclusions.
"""
from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image


BODY=Path(__file__).parent/'fixtures/oge_privacy_2019_body.txt'


def native_privacy(path, *, mutation=None, footer_name='EXAMPLE, PERSON'):
    document=pymupdf.open();page=document.new_page(width=842,height=595)
    for i,line in enumerate(BODY.read_text(encoding='utf-8').splitlines()):
        y=51 if i==0 else 66 if i==1 else 77+(i-2)*11
        # The official form includes a single space glyph on its blank line.
        page.insert_text((36,y),line or ' ',fontsize=10,fontname='helv')
    page.draw_line((36,352),(806,352),color=(.75294,.75294,.75294),width=1)
    footer=footer_name+' - Page 1'
    x=806-pymupdf.get_text_length(footer,fontname='helv',fontsize=10)
    if mutation=='footer_second_run':
        first,second=footer.split(' - Page ')
        first+=' - Page '
        page.insert_text((x,559),first,fontname='helv',fontsize=10)
        page.insert_text((x+pymupdf.get_text_length(first,fontname='helv',fontsize=10),559),second,fontname='helv',fontsize=10)
    else:
        offset=(-1,0)if mutation=='footer_shift_left'else (0,-1)if mutation=='footer_shift_up'else (0,0)
        page.insert_text((x+offset[0],559+offset[1]),footer,fontname='helv',fontsize=10)
    if mutation=='footer_adjacent_show':
        # The observed official writer emits the page number in a second Tj,
        # with no intervening positioning/state operation. This remains one run.
        xref=page.get_contents()[-1];stream=document.xref_stream(xref)
        old=(b'[<'+footer.encode().hex().encode()+b'>]TJ')
        replacement=b'<'+footer[:-1].encode().hex().encode()+b'>Tj\n<31>Tj'
        assert old in stream
        document.update_stream(xref,stream.replace(old,replacement))
    # PDF defaults linewidth=1. Make it explicit so both renderers' object
    # inventories describe the source line consistently.
    for xref in page.get_contents():document.update_stream(xref,b'1 w\n'+document.xref_stream(xref))
    if mutation=='image':
        image=Image.new('RGB',(100,100),'white');raw=BytesIO();image.save(raw,format='PNG')
        page.insert_image(pymupdf.Rect(400,400,500,500),stream=raw.getvalue())
    elif mutation=='extra_text':page.insert_text((40,420),'Sale ACME $15,000 05/01/2026',fontsize=10)
    elif mutation=='footer_text':page.insert_text((40,520),'Additional disclosure',fontsize=10)
    elif mutation=='footer_extra_run':page.insert_text((40,559),'ACME FUND',fontsize=10,fontname='helv')
    elif mutation=='footer_adjacent_extra_run':
        extra='ACME FUND'
        page.insert_text((x-pymupdf.get_text_length(extra,fontname='helv',fontsize=10),559),extra,fontsize=10,fontname='helv')
    elif mutation=='footer_other_baseline':page.insert_text((40,549),'ACME FUND',fontsize=10,fontname='helv')
    elif mutation=='line':page.draw_line((30,400),(800,400),width=1)
    elif mutation=='white_box':page.draw_rect(pymupdf.Rect(30,80,820,340),fill=(1,1,1),color=None)
    elif mutation=='annotation':page.add_text_annot((500,400),'ACME sale')
    elif mutation in ('invisible','clipped'):
        xref=page.get_contents()[0]
        prefix=b'3 Tr\n' if mutation=='invisible' else b'0 0 0 0 re W n\n'
        document.update_stream(xref,prefix+document.xref_stream(xref))
    elif mutation=='white_text':
        xref=page.get_contents()[0];document.update_stream(xref,b'1 g\n'+document.xref_stream(xref))
    document.save(path);document.close()


