"""Normal entry point for source-bound Senate paper slot observations."""
from functools import partial
import json
from pathlib import Path
import subprocess
import tempfile

from .senate_observation_data import DEFAULT_ROOT


def configure_parser(parser):
    for name in ('evidence-root','extraction-batch','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--observations-root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--pending-output',type=Path)
    parser.add_argument('--date-ocr-executable',default='tesseract')
    parser.add_argument('--ocr-cache-root',type=Path,required=True)


def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent,suffix='.tmp',delete=False) as handle:
        handle.write(raw);temporary=Path(handle.name)
    temporary.replace(path)


def saved_date_reads(path,observations_root):
    from .senate_paper import PAPER_OCR_RULE_VERSION
    from .senate_paper_observation import SCHEMA,project_pending_slots
    if not path.is_file():
        return []
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version') != SCHEMA:
        return []
    # Check saved page/slot/relationship bindings before adopting the old OCR
    # observations. The classifier still hashes actual current page pixels.
    project_pending_slots(value,observations_root=observations_root)
    reads=[]
    for report in value['reports']:
        for slot in report.get('slots',[]):
            cell=slot.get('key_cells',{}).get('date',{})
            result=cell.get('cell_ocr')
            if not result or result.get('engine') != 'multi-pass-strict-date-shadow':
                continue
            if set(result.get('variants',{})) != {'x1_plain_psm7','x2_digits_psm7','x3_digits_psm7','x4_digits_psm7'}:
                raise ValueError('Saved Senate OCR consensus is incomplete')
            x0,y0,x1,y1=cell['box_px']
            reads.append({'binding':{'parser_version':PAPER_OCR_RULE_VERSION,'mode':'L',
                'size':[x1-x0,y1-y0],'pixel_sha256':cell['grayscale_sha256']},
                'result':result,'engine_version':'legacy saved Tesseract consensus; binary version not recorded'})
    return reads


def run(args):
    from .ocr_cache import cached_image_reader
    from .senate_paper import PAPER_OCR_RULE_VERSION
    from .senate_paper_observation import compare_archived_batch,project_pending_slots
    from .senate_slot_inventory import tesseract_date_consensus
    version=subprocess.check_output([args.date_ocr_executable,'--version'],timeout=10).decode('utf-8').splitlines()[0]
    reader=cached_image_reader(args.ocr_cache_root,parser_version=PAPER_OCR_RULE_VERSION,
        engine_version=version,reader=partial(tesseract_date_consensus,executable=args.date_ocr_executable),
        saved_reads=saved_date_reads(args.output,args.observations_root))
    batch=json.loads(args.extraction_batch.read_text(encoding='utf-8'))
    result=compare_archived_batch(args.evidence_root,args.observations_root,batch,date_reader=reader)
    _write(args.output,result)
    if args.pending_output is not None:
        _write(args.pending_output,project_pending_slots(result,observations_root=args.observations_root))
    print(json.dumps({key:result[key] for key in ('report_count','observed_report_count',
        'coverage_unknown_report_count','physical_grid_slot_count','observed_page_count',
        'unobserved_page_count','label_counts')},sort_keys=True))
