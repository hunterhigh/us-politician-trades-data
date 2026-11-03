"""Keep large partial-holding evidence content-addressed outside the manifest."""
import json
import re
from .codec import encode, digest

REFERENCE_SCHEMA = 'holding-report-coverage-reference/v1'
MAX_BYTES = 4 * 1024 * 1024


def attach(manifest, files):
    coverage = manifest.get('holding_report_coverage')
    if coverage is None or len(encode(manifest)) <= 16 * 1024:
        return
    raw = encode(coverage)
    if not isinstance(coverage, list) or len(raw) > MAX_BYTES:
        raise ValueError('Holding coverage exceeds its public size budget')
    sha = digest(raw)
    files['status/'+sha+'.json'] = raw
    manifest['holding_report_coverage'] = {'schema_version':REFERENCE_SCHEMA,
        'sha256':sha,'byte_length':len(raw)}


def restore(manifest, read):
    ref = manifest.get('holding_report_coverage')
    if not isinstance(ref, dict):
        return
    if (set(ref) != {'schema_version','sha256','byte_length'} or ref['schema_version'] != REFERENCE_SCHEMA or
            not isinstance(ref['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',ref['sha256']) or
            type(ref['byte_length']) is not int or not 0 < ref['byte_length'] <= MAX_BYTES):
        raise ValueError('Invalid holding coverage reference')
    raw = read('status/'+ref['sha256']+'.json',MAX_BYTES)
    if len(raw) != ref['byte_length'] or digest(raw) != ref['sha256']:
        raise ValueError('Holding coverage byte length or hash mismatch')
    coverage = json.loads(raw)
    if not isinstance(coverage,list):
        raise ValueError('Holding coverage must be an array')
    manifest['holding_report_coverage'] = coverage
