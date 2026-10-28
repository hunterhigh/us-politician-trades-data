"""Apply source-bound human cover-date observations to OGE candidate input."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import Path

from .oge import OgeCatalogError
from .corrections import apply_correction, load_correction


SCHEMA = "oge-manual-cover-date/v1"
DATE_REASON = "filer_signature_date_not_unique"
CORRECTIONS_ROOT = Path(__file__).resolve().parents[2] / 'data/corrections/oge_cover_dates'
PROFILE = 'reviewed-cover-date/v1'


def _registered(extraction):
    document = extraction.get('document_id')
    if (not isinstance(document, str) or len(document) != 32 or
            any(c not in '0123456789abcdef' for c in document)):
        return None
    path = CORRECTIONS_ROOT / (document + '.json')
    if not path.is_file():
        return None
    value = load_correction(path)
    if (value.get('profile') != PROFILE or value['source'] != 'oge' or
            value['document_id'] != document or
            value['source_sha256'] != extraction.get('source_sha256') or
            value['source_url'] != extraction.get('source_url') or
            extraction.get('parser_version') != value['parser_version']):
        raise OgeCatalogError('OGE cover-date correction source binding changed')
    return value


def load_observations(path):
    """Read shipped correction data or an explicit new human observation file."""
    if not path.is_dir():
        return json.loads(path.read_text(encoding='utf-8'))
    observations = []
    for file in sorted(path.glob('*.json')):
        correction = load_correction(file)
        if correction.get('profile') != PROFILE:
            raise OgeCatalogError('OGE cover-date correction profile is unsupported')
        observations.append(correction['observation'])
    return {'schema_version': 'oge-manual-cover-date-observations/v1',
            'status': 'observation_only', 'observations': observations}


def apply_manual_cover_dates(extractions: list[dict], root: Path) -> list[dict]:
    """Resolve only the filing-date reason; leave all other evidence and rows intact."""
    selected: list[dict] = []
    for extraction in extractions:
        document_id = extraction.get("document_id")
        source_sha = extraction.get("source_sha256")
        if not isinstance(document_id, str) or not isinstance(source_sha, str):
            raise OgeCatalogError("OGE extraction lacks manual-date source identity")
        if extraction.get('parser_version') not in {None, 'oge-278t-pdf/v2'}:
            selected.append(extraction)
            continue
        correction = _registered(extraction)
        path = root / document_id / f"{source_sha}.json"
        if not path.is_file():
            selected.append(extraction)
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if correction is not None and value != correction['observation']:
            raise OgeCatalogError('OGE cover-date sidecar differs from correction data')
        if (value.get("schema_version") != SCHEMA or
                value.get("status") != "observation_only" or
                value.get("qualification_effect") != "none" or
                value.get("document_id") != document_id or
                value.get("source_sha256") != source_sha or
                value.get("source_url") != extraction.get("source_url") or
                value.get("extraction_parser_version") != "oge-278t-pdf/v2" or
                value.get("page_number") != 1 or
                value.get("recognition_method") != "human_visual_reading_of_pdf_cover" or
                not isinstance(value.get("raw_reading"), str) or
                not value["raw_reading"].strip()):
            raise OgeCatalogError("OGE manual cover date is not bound to the extraction")
        role = value.get("field_role")
        observed = value.get("observed_date")
        try:
            if not isinstance(observed, str) or date.fromisoformat(observed).isoformat() != observed:
                raise ValueError
        except ValueError:
            raise OgeCatalogError("OGE manual cover date is invalid") from None
        if role == "amendment_signature_date":
            if value.get("filed_at") is not None:
                raise OgeCatalogError("OGE amendment signature is not a filing date")
            selected.append(extraction)
            continue
        if role != "filer_signature_date" or value.get("filed_at") != observed:
            raise OgeCatalogError("OGE manual filing date cannot resolve this extraction")
        if (extraction.get("filed_at") == observed and
                DATE_REASON not in extraction.get("document_reasons", [])):
            selected.append(extraction)
            continue
        if (extraction.get("filed_at") is not None or
                DATE_REASON not in extraction.get("document_reasons", [])):
            raise OgeCatalogError("OGE manual filing date conflicts with extraction")
        reasons = [r for r in extraction['document_reasons'] if r != DATE_REASON]
        evidence = {
            "review_path": f"oge/manual_cover_dates/{document_id}/{source_sha}.json",
            "page_number": 1,
            "source_sha256": source_sha,
            "raw_reading": value["raw_reading"],
            "observed_date": observed,
        }
        if correction is None:
            # Newly supplied human sidecars use the same asserted engine. They
            # remain subject to the existing archived-source producer boundary.
            changes = {'filed_at': observed, 'document_reasons': reasons,
                'evidence_complete': not reasons, 'manual_filing_date_evidence': evidence}
            correction = {'source': 'oge', 'document_id': document_id,
                'source_sha256': source_sha, 'actions': [
                    {'op': 'set_report', 'field': field, 'expect': extraction.get(field),
                     'expect_present': field in extraction, 'value': changed,
                     'evidence': {'page': 1, 'field_role': role, 'raw_reading': value['raw_reading']}}
                    for field, changed in changes.items()]}
        if any(a['op'] != 'set_report' or a.get('field') not in
                {'filed_at', 'document_reasons', 'evidence_complete', 'manual_filing_date_evidence'}
                for a in correction['actions']):
            raise OgeCatalogError('OGE cover-date correction has an unsupported action')
        model = {'source': 'oge', 'document_id': document_id, 'source_sha256': source_sha,
                 'rows': {}, 'report': extraction}
        try:
            resolved = apply_correction(model, correction)['report']
        except (ValueError, KeyError, TypeError) as error:
            raise OgeCatalogError('OGE cover-date correction precondition failed: ' + str(error)) from error
        expected = {**deepcopy(extraction), 'filed_at': observed, 'document_reasons': reasons,
                    'evidence_complete': not reasons, 'manual_filing_date_evidence': evidence}
        if resolved != expected:
            raise OgeCatalogError('OGE cover-date correction changes unrelated source facts')
        selected.append(resolved)
    return selected
