"""Cache deterministic image reads without granting them fact authority."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from .codec import digest, encode


def cached_image_reader(root, *, parser_version, engine_version, reader, saved_reads=()):
    root = Path(root)
    seeds = {}
    for item in saved_reads:
        identity = digest(encode(item['binding']))
        if identity in seeds and seeds[identity]['result'] != item['result']:
            raise ValueError('Saved OCR observations disagree for identical source pixels')
        seeds[identity] = item

    def read(image):
        binding = {'parser_version':parser_version,
            'mode':image.mode,'size':list(image.size),'pixel_sha256':digest(image.tobytes())}
        identity = digest(encode(binding))
        path = root/(identity+'.json')
        if path.is_file():
            if path.stat().st_size > 64*1024:
                raise ValueError('Saved OCR read exceeds its byte budget')
            saved = json.loads(path.read_text(encoding='utf-8'))
            if (saved.get('schema_version') != 'saved-image-read/v1' or saved.get('binding') != binding or
                    digest(encode(saved.get('result'))) != saved.get('result_sha256')):
                raise ValueError('Saved OCR read no longer matches its source binding')
            return deepcopy(saved['result'])
        seed = seeds.get(identity)
        if seed is not None and seed['binding'] != binding:
            raise ValueError('Saved OCR observation source binding changed')
        result = deepcopy(seed['result']) if seed is not None else reader(image)
        recorded_engine = seed['engine_version'] if seed is not None else engine_version
        raw = encode({'schema_version':'saved-image-read/v1','binding':binding,'engine_version':recorded_engine,
            'result':result,'result_sha256':digest(encode(result))})
        if len(raw) > 64*1024:
            raise ValueError('OCR read exceeds its byte budget')
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=root,suffix='.tmp',delete=False) as handle:
            handle.write(raw);temporary=Path(handle.name)
        temporary.replace(path)
        return result
    return read
