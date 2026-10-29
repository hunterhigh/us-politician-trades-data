"""Generated direct-OGE source using existing six-column/cover primitives.

The saved words/TSV are construction-controlled observations, not an OCR
accuracy claim. No recognizer is run. Source pixels, grids and warps are real.
"""
from copy import deepcopy
import os,subprocess,sys,tempfile,unittest
import hashlib,io,json
from pathlib import Path

EDGES=[104.,150.,473.,524.,581.,628.,686.]

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')

def actual_source_quad_fixture(root:Path):
    import pdfplumber,pymupdf
    from unison_snapshot.ocr_geometry import OcrPage
    from unison_snapshot.oge_reports import parse_archived_pdf
    from unison_snapshot.oge_278t_raster_slots import observe_raster_slots,DUAL_MODE
    from tempfile import TemporaryDirectory
    from test_oge_cover_consumption import fixture as cover_fixture
    with TemporaryDirectory(prefix='source-quad-cover-') as temporary:
        seed=cover_fixture(Path(temporary))
        catalog=deepcopy(seed['catalog'])
        record=catalog['transactions'][0];document=record['source_document_id']
        metadata=deepcopy(seed['metadata']);original_bytes=seed['source'].read_bytes()
        catalog_files={p.relative_to(seed['evidence']):p.read_bytes()
                       for p in (seed['evidence']/'oge/catalog').rglob('*') if p.is_file()}
        status_bytes=(seed['review']/'status/oge.json').read_bytes()
    with pymupdf.open() as body:
        page=body.new_page(width=792,height=612)
        for x in EDGES:page.draw_line((x,85),(x,581),width=.5)
        for y in [85,*range(116,582,15)]:page.draw_line((104,y),(686,y),width=.5)
        page.insert_text((104,26),'OGE Form 278-T 2024',fontsize=8)
        for x,label in zip(EDGES[1:],['Description','Type','Date','Notification','Amount']):
            page.insert_text((x+3,111),label,fontsize=8)
        for x,text in zip(EDGES,['1','ALPHA Fund','Purchase','06/01/2026','No','$50,001 -']):
            page.insert_text((x+4,124),text,fontsize=6)
        page.insert_text((EDGES[5]+4,129),'$100,000',fontsize=6)
        vector=body.tobytes()
        image=page.get_pixmap(matrix=pymupdf.Matrix(3,3),alpha=False).tobytes('png')
    with pdfplumber.open(io.BytesIO(vector)) as pdf:
        truth=pdf.pages[0].extract_words(x_tolerance=2,y_tolerance=2)
    with pymupdf.open(stream=original_bytes,filetype='pdf') as pdf:
        while len(pdf)>1:pdf.delete_page(1)
        page=pdf.new_page(width=792,height=612)
        page.insert_image(page.rect,stream=image)
        page.insert_text((104,10),'Source quad contract fixture',fontsize=6)
        raw=pdf.tobytes()
    sha=hashlib.sha256(raw).hexdigest()
    evidence,review=root/'evidence',root/'review'
    path=evidence/f'oge/reports/{document}/{sha}.pdf';path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    metadata={**metadata,'sha256':sha,'byte_length':len(raw),
              'archive_path':path.relative_to(evidence).as_posix()}
    write(path.with_suffix('.json'),metadata)
    primary_words=[]
    for number,word in enumerate(truth,1):
        text=word['text']
        if text=='Purchase':text='?'
        if text in {'$50,001','$100,000'}:text='?'
        primary_words.append({**word,'text':text,'ocr_confidence':95.,'block_num':1,
                              'par_num':1,'line_num':number})
    observation=observe_raster_slots(path,expected_sha256=sha,page_numbers=[2],resolution=300,
        saved_ocr_pages={2:OcrPage(width=792,height=612,words=primary_words)},
        saved_ocr_engine='construction-controlled primary words; no OCR',geometry_mode=DUAL_MODE)
    for relative,raw_catalog in catalog_files.items():
        target=evidence/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw_catalog)
    (review/'status').mkdir(parents=True);(review/'status/oge.json').write_bytes(status_bytes)
    parsed=parse_archived_pdf(evidence,path.with_suffix('.json'))
    generic=parse_archived_pdf(evidence,path.with_suffix('.json'),generic=True)
    assert parsed['parser_version']=='oge-278t-pdf/v2' and generic['parser_version']=='oge-278t-pdf/v7'
    for value in [parsed,generic]:
        write(review/f'oge/extractions/{document}/{sha}/{value["parser_version"].replace("/","-")}.json',value)
    write(root/'primary.json',observation);write(root/'catalog.json',catalog)
    write(root/'construction-truth-words.json',truth)
    return {'root':root,'evidence':evidence,'review':review,'source':path,'metadata_path':path.with_suffix('.json'),
            'metadata':metadata,'record':record,'catalog':catalog,'old':parsed,'generic':generic,
            'observation':observation,'truth_words':truth,'sha':sha,'document':document}

def actual_source_quad_secondary(data,field,rendered):
    import numpy as np,math
    from unison_snapshot.oge_278t_source_quad_cells import source_quad_cell,observe_saved_source_quad_cells
    page=data['observation']['pages'][0]
    slot=next(s for s in page['slots'] if s['words'])
    recipe,pixels=source_quad_cell(rendered,page,slot,field,layer='full_source')
    # Known construction word boxes are mapped through the *actual* source
    # affine and warp. No fixed pixel recipe or synthetic render is accepted.
    affine=rendered['receipt']['pixel_edge_to_source_pdf_affine']
    media=rendered['receipt']['source_mediabox_pdf_bottom_left']
    inverse=np.linalg.inv(np.array([[affine[0],affine[2],affine[4]],
                                    [affine[1],affine[3],affine[5]],[0.,0.,1.]]))
    warp=np.array(recipe['source_pixel_to_cell_matrix'])
    words=[w for w in data['truth_words'] if w['top']>116 and
           ((field=='type' and EDGES[2]<w['x0']<EDGES[3]) or
            (field=='amount' and EDGES[5]<w['x0']<EDGES[6]))]
    tsv=['level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext']
    for index,w in enumerate(words,1):
        points=[]
        for x,y in [(w['x0'],w['top']),(w['x1'],w['top']),(w['x1'],w['bottom']),(w['x0'],w['bottom'])]:
            p=inverse@np.array([x+media[0],media[3]-y,1.]);p=warp@np.array([p[0]/p[2],p[1]/p[2],1.])
            points.append((p[0]/p[2]+recipe['padding'],p[1]/p[2]+recipe['padding']))
        left,top=math.floor(min(p[0] for p in points)),math.floor(min(p[1] for p in points))
        right,bottom=math.ceil(max(p[0] for p in points)),math.ceil(max(p[1] for p in points))
        line=1 if w['top']<122 else 2
        tsv.append(f'5\t1\t1\t1\t{line}\t{index}\t{left}\t{top}\t{right-left}\t{bottom-top}\t95\t{w["text"]}')
    text='\n'.join(tsv)+'\n'
    recipe.update(physical_index=slot['slot_index']-1,column=2 if field=='type' else 4,
                  tsv_sha256=hashlib.sha256(text.encode()).hexdigest())
    saved=[{'page_number':2,'render_receipt':rendered['receipt'],'selected_slot_indices':[slot['slot_index']],
            'cells':[{'slot_index':slot['slot_index'],'recipe':recipe,'tsv_utf8':text}]}]
    return observe_saved_source_quad_cells(data['source'],slots_observation=data['observation'],field=field,
        saved_pages=saved,layer='full_source',ocr_engine='construction-controlled saved secondary TSV; no OCR')


ROOT=Path(__file__).resolve().parents[2]
CUTOFF='2026-06-20T00:00:00Z'




if __name__=='__main__':unittest.main()
