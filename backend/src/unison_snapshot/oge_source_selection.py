"""Select a stored native input, without asserting version recency or replacement."""
from __future__ import annotations

from pathlib import Path
import re

from .oge import OgeCatalogError

MAX_DOCUMENT_SOURCES = 32
MAX_CACHE_BYTES = 16 * 1024 * 1024
_SELECTION = re.compile(r'([0-9a-f]{32}):([0-9a-f]{64})')


def parse_document_sources(values):
    if not isinstance(values, list) or len(values) > MAX_DOCUMENT_SOURCES:
        raise OgeCatalogError('OGE document sources must be a list of at most 32 DOC:SHA values')
    result = {}
    for value in values:
        match = _SELECTION.fullmatch(value) if isinstance(value, str) else None
        if match is None or match[1] in result:
            raise OgeCatalogError('OGE document sources require unique lowercase DOC:SHA identities')
        result[match[1]] = match[2]
    return result


def verify_selected_native_cache(path, extraction_root, value, evidence_root):
    """Authenticate a canonical v2/v7 input against its actual stored original.

    This does not choose between originals, establish a newer version, or
    authorize changes to old facts. Those existing consumer rules still apply.
    """
    from .oge_reports import (PARSER_VERSION, GENERIC_PARSER_VERSION,
                              _read_archived_source, selected_parser_version, parse_archived_pdf)
    document, sha, version = (value.get(key) for key in
                              ('document_id', 'source_sha256', 'parser_version'))
    if (not isinstance(document, str) or not isinstance(sha, str)
            or _SELECTION.fullmatch(document + ':' + sha) is None
            or version not in {PARSER_VERSION, GENERIC_PARSER_VERSION}):
        raise OgeCatalogError('Explicit OGE document sources support ordinary native v2/v7 inputs only')
    root, source = Path(extraction_root).resolve(), Path(path)
    expected = root / document / sha / (version.replace('/', '-') + '.json')
    if (source.absolute() != source.resolve() or source.resolve() != expected
            or not source.is_file() or source.stat().st_size > MAX_CACHE_BYTES):
        raise OgeCatalogError('Selected OGE source cache is not a bounded canonical input')
    evidence = Path(evidence_root).resolve()
    relative = f'oge/reports/{document}/{sha}.pdf'
    metadata_path = evidence / relative.replace('.pdf', '.json')
    metadata, pdf, actual = _read_archived_source(evidence, metadata_path)
    if (actual != sha or metadata.get('document_id') != document
            or metadata.get('archive_path') != relative
            or metadata.get('document_url') != value.get('source_url')
            or selected_parser_version(metadata, pdf, sha) != PARSER_VERSION
            or selected_parser_version(metadata, pdf, sha, generic=True) != GENERIC_PARSER_VERSION
            or parse_archived_pdf(evidence, metadata_path,
                                  generic=version == GENERIC_PARSER_VERSION) != value):
        raise OgeCatalogError('Selected OGE native cache differs from its complete original replay')
    return source, metadata_path, pdf
