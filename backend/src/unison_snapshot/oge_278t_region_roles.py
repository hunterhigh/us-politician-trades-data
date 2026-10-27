"""Dispatch the bounded native region readers, without page/fact permission."""


def observe_native_regions(page, page_number, *, source_sha256, cover_identity_context=None):
    from .oge_278t_summary_region import observe_native_summary_region
    from .oge_278t_endnote_region import observe_native_endnote_region
    summary = observe_native_summary_region(page, page_number, source_sha256=source_sha256)
    notes = observe_native_endnote_region(page, page_number, source_sha256=source_sha256,
        cover_identity_context=cover_identity_context)
    return sorted([item for item in (summary, notes) if item['status'] == 'observed'],
                  key=lambda item: item['region_bbox_pdf_points'])


def replay_native_region(path, proof, *, source_sha256, page_number, cover_identity_context=None):
    from .oge_278t_summary_region import REGION_RULE as SUMMARY, replay_native_summary_region
    from .oge_278t_endnote_region import REGION_RULE as ENDNOTES, replay_native_endnote_region
    if not isinstance(proof, dict):
        return False
    if proof.get('rule_version') == SUMMARY:
        return replay_native_summary_region(path, proof,
            source_sha256=source_sha256, page_number=page_number)
    if proof.get('rule_version') == ENDNOTES:
        return replay_native_endnote_region(path, proof, source_sha256=source_sha256,
            page_number=page_number, cover_identity_context=cover_identity_context)
    return False
