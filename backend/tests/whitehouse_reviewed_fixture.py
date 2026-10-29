"""Fixture metadata comes from the shipped correction data."""
from unison_snapshot.reviewed_whitehouse_ocr import profiles, source_profiles

_COVER = next(c for c in profiles() if c['output_mode'] == 'source_cover_gate')
COVER_DOCUMENT_ID = _COVER['document_id']
COVER_SOURCE_URL = _COVER['source_url']
COVER_SOURCE_SHA256 = _COVER['source_sha256']
COVER_FILED_AT = _COVER['source_profile']['report_date']
LISTING_SOURCE_PROFILES = source_profiles(mode='source_listing_evidence')
