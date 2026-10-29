"""Verify the installed locked dependencies and a deterministic English OCR read."""
import importlib
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

requirements=Path(sys.argv[1])
versions={}
for line in requirements.read_text(encoding='utf-8').splitlines():
    if match:=re.match('([A-Za-z0-9._-]+)==([^ ]+)',line):
        package,expected=match.groups();actual=version(package)
        if actual!=expected:raise ValueError('Runtime dependency differs: '+package)
        versions[package]=actual
for module in ('pdfplumber','pymupdf','pypdfium2','fontTools','PIL','numpy','cv2','onnxruntime'):
    importlib.import_module(module)
node=subprocess.check_output(['node','--version'],text=True).strip()
assert node=='v24.14.1',node
engine=subprocess.check_output(['tesseract','--version'],text=True).splitlines()[0]
assert engine=='tesseract 5.3.4',engine
assert sys.version_info[:3]==(3,11,17),sys.version
pins=json.loads((requirements.resolve().parents[1]/'runtime/inputs.lock.json').read_bytes())
if hashlib.sha256(requirements.read_bytes()).hexdigest()!=pins['requirements_sha256']:
    raise ValueError('Dependency lock differs from runtime inputs')
for name in ('eng','osd'):
    if hashlib.sha256(Path('/usr/local/share/tessdata',name+'.traineddata').read_bytes()).hexdigest()!=pins[name+'_sha256']:
        raise ValueError('Runtime trained data changed: '+name)
if '--metadata-only' in sys.argv:
    lock=json.loads((requirements.resolve().parents[1]/'runtime/image.lock.json').read_bytes())
    actual=hashlib.sha256((requirements.resolve().parents[1]/'runtime/inputs.lock.json').read_bytes()).hexdigest()
    if lock['inputs_sha256']!=actual or lock['requirements_sha256']!=pins['requirements_sha256']:
        raise ValueError('Selected runtime image does not match these fixed inputs')
read=None
from PIL import Image,ImageDraw,ImageFont
if '--metadata-only' not in sys.argv:
    with tempfile.TemporaryDirectory() as folder:
        image=Image.new('RGB',(1000,180),'white')
        ImageDraw.Draw(image).text((40,30),'TEST 123',fill='black',
            font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',90))
        path=Path(folder)/'smoke.png';image.save(path)
        read=subprocess.check_output(['tesseract',str(path),'stdout','--psm','7','-l','eng'],text=True).strip()
        assert read=='TEST 123',read
print(json.dumps({'python':sys.version.split()[0],'node':node,'tesseract':engine,
    'ocr_read':read,'versions':versions},sort_keys=True))
