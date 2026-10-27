"""Actual normal parser identities for a same-PDF White House/direct pair.

Generated native PDFs are source/consumer controls, not official accuracy gold.
No formal ID or financial field is rewritten to manufacture an equivalence.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from scripts import build_whitehouse_278t_candidate as whitehouse_cli
from test_whitehouse_owner_correction import encoded, fixture
from unison_snapshot.oge import archive_catalog, build_catalog, parse_catalog_page
from unison_snapshot.oge_candidate import build_oge_candidate
from unison_snapshot.oge_reports import parse_archived_pdf
from unison_snapshot.oge_whitehouse import coverage_from_archive


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else encoded(value))


def make_exact_duplicate_inputs(root, *, include_unrelated=False, identical_rows=False,
                                profile='plain', historical_wh=False):
    """Return two genuine old IDs plus canonical source roots and bindings.

    The White House fact retains its actual v2-derived ID after normal v5 owner
    correction. The canonical fact comes from an actual fresh direct v7 build.
    ``include_unrelated`` adds an independently generated old source fact for
    the consumption tests; the two-fact core fixture remains minimal.
    ``historical_wh`` selects the actual preliminary legacy-base object rather
    than changing any fields on its normally finalized White House counterpart.
    """
    root = Path(root)
    evidence, review = root / 'evidence', root / 'review'
    if profile not in {'plain', 'endnote', 'account'}:
        raise ValueError('Unknown exact duplicate source fixture profile')
    if profile == 'account':
        if identical_rows:
            raise ValueError('The account control retains the existing single-account fixture scope')
        from whitehouse_account_display_fixture import fixture as account_fixture
        _, _, review_files, evidence_files, previous = account_fixture()
    else:
        asset = ('Example Common Stock See Endnote (EXM)' if profile == 'endnote'
                 else 'Example Common Stock (EXM)')
        _, _, review_files, evidence_files, previous = fixture(asset=asset,
            extra='same_financial' if identical_rows else None)
    for folder, files in ((evidence, evidence_files), (review, review_files)):
        for relative, value in files.items():
            save(folder / relative, value)

    # Use the exact existing method control: the official directory date and
    # the actual native cover agree; no date reader or field value is changed.
    payload = json.loads(next(raw for relative, raw in evidence_files.items()
                              if relative.startswith('oge/catalog/pages/')))
    payload['data'][0]['docDate'] = '2026-06-13T00:00:00'
    catalog = build_catalog([parse_catalog_page(payload, start=0, length=1)])
    catalog_metadata = archive_catalog(evidence,
        [(0, 1, encoded(payload), {'content-type': 'application/json'})],
        catalog, retrieved_at='2026-06-20T00:00:00Z')
    save(review / 'oge/whitehouse/coverage-current.json',
         coverage_from_archive(evidence, evidence / catalog_metadata['archive_path']))
    record = catalog['transactions'][0]
    wh_relative, pdf_bytes = next((relative, raw) for relative, raw in evidence_files.items()
                                  if relative.endswith('.pdf'))
    wh_pdf = evidence / wh_relative
    whitehouse_archive = json.loads(wh_pdf.with_suffix('.json').read_bytes())
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    document = record['source_document_id']
    relative = f'oge/reports/{document}/{sha}.pdf'
    oge_archive = {
        'schema_version': 'oge-278t-archive/v1', 'source_id': 'oge',
        'document_id': document, 'document_url': record['document_url'],
        'sha256': sha, 'byte_length': len(pdf_bytes), 'archive_path': relative,
        'headers': {}, 'retrieved_at': '2026-06-20T00:00:00Z',
        **{key: record[key] for key in ('filer_name', 'agency', 'position_title',
            'catalog_added_date', 'amended_label', 'pending_final_oge_disposition')},
    }
    source = evidence / relative
    save(source, pdf_bytes)
    save(source.with_suffix('.json'), oge_archive)
    direct = []
    for generic in (False, True):
        parsed = parse_archived_pdf(evidence, source.with_suffix('.json'), generic=generic)
        expected = 'oge-278t-pdf/v7' if generic else 'oge-278t-pdf/v2'
        if parsed['parser_version'] != expected:
            raise AssertionError('The exact duplicate control requires the existing ordinary v2/v7 pair')
        save(review / f'oge/extractions/{document}/{sha}/{expected.replace("/", "-")}.json', parsed)
        direct.append(parsed)
    empty = {**deepcopy(previous), 'transactions': [], 'people': []}
    canonical, direct_audit = build_oge_candidate(catalog, [direct[1]], empty,
        data_cutoff_at=previous['meta']['data_cutoff_at'], evidence_root=evidence)
    save(review / 'oge/qualifications/current.json', direct_audit)
    save(review / 'status/oge.json', {'catalog_sha256': catalog_metadata['sha256']})
    save(root / 'legacy-base.json', previous)
    # The normal preliminary build applies only existing approved source
    # corrections. Both its final and actual legacy-base objects are retained.
    whitehouse_cli.run(oge_candidate=root / 'legacy-base.json',
        candidate_out=root / 'whitehouse-corrected.json', audit_out=root / 'owner-audit.json',
        evidence_root=evidence, review_root=review, expected_report_count=1)
    corrected = json.loads((root / 'whitehouse-corrected.json').read_bytes())
    expected_count = 2 if identical_rows else 1
    if len(corrected['transactions']) != expected_count or len(canonical['transactions']) != expected_count:
        raise AssertionError('The generated exact-copy control must preserve every printed source row')
    native = json.loads(next(raw for relative, raw in review_files.items() if 'native-roles' in relative))
    canonical_by_id = {fact['id']: fact for fact in canonical['transactions']}
    duplicate_by_id = {fact['id']: fact for fact in corrected['transactions']}
    historical_by_id = {fact['id']: fact for fact in previous['transactions']}
    direct_slots = {}
    for row in direct[1]['transactions']:
        key = row['page_number'], row['row_number']
        if key in direct_slots:
            raise AssertionError('The generated direct source repeats a physical row')
        direct_slots[key] = row
    pairs = []
    for row in native['transactions']:
        key = row['page_number'], row['row_number']
        direct_row = direct_slots[key]
        canonical_fact = deepcopy(canonical_by_id[direct_row['extraction_id']])
        final_duplicate = deepcopy(duplicate_by_id[row['extraction_id']])
        historical_duplicate = deepcopy(historical_by_id[row['extraction_id']])
        differences = {field for field in canonical_fact.keys() | final_duplicate.keys()
                       if canonical_fact.get(field) != final_duplicate.get(field)}
        if differences != {'id', 'source_url', 'filing_id'}:
            raise AssertionError(f'Generated source facts are not complete exact duplicates: {differences}')
        duplicate_fact = historical_duplicate if historical_wh else final_duplicate
        correction_fields = sorted(field for field in canonical_fact.keys() | duplicate_fact.keys()
            if field not in {'id', 'source_url', 'filing_id'} and
            canonical_fact.get(field) != duplicate_fact.get(field))
        allowed = {'plain': {'owner'}, 'endnote': {'owner', 'instrument_type'},
                   'account': {'owner', 'asset_name'}}[profile]
        if not set(correction_fields) <= allowed:
            raise AssertionError('Actual historical producer differs outside the existing approved correction')
        pairs.append({'page_number': key[0], 'row_number': key[1],
                      'canonical_fact': canonical_fact, 'duplicate_fact': duplicate_fact,
                      'historical_duplicate_fact': historical_duplicate,
                      'final_duplicate_fact': final_duplicate,
                      'source_correction_fields': correction_fields})
    pairs.sort(key=lambda pair: (pair['page_number'], pair['row_number']))
    if len(pairs) != expected_count:
        raise AssertionError('The generated source pairing lost a physical row')
    canonical_facts = [pair['canonical_fact'] for pair in pairs]
    duplicate_facts = [pair['duplicate_fact'] for pair in pairs]
    canonical_fact, duplicate_fact = canonical_facts[0], duplicate_facts[0]
    base = deepcopy(canonical)
    base['transactions'].extend(deepcopy(duplicate_facts))
    unrelated = None
    if include_unrelated:
        # Existing actual producer fixture, a distinct source and a real ID.
        # Its financial facts and person are kept byte-for-byte by this test.
        import test_oge_owner_correction as other
        other_document = 'b' * 32
        other_url = (f'https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/{other_document}'
                     '/$FILE/Other.pdf')
        with patch.object(other, 'DOCUMENT', other_document), patch.object(other, 'URL', other_url):
            unrelated, _, other_review, other_evidence, other_base = other.fixture(
                asset='Unrelated Mutual Fund (UMF)', filer_name='Other, Bea')
        for folder, files, marker in ((evidence, other_evidence, '/reports/'),
                                       (review, other_review, '/extractions/')):
            for name, raw in files.items():
                if marker in name:
                    save(folder / name, raw)
        base['transactions'].append(deepcopy(unrelated))
        known = {person['id']: person for person in base['people']}
        for person in other_base['people']:
            if person['id'] in known:
                if known[person['id']] != person:
                    raise AssertionError('Generated old person identity differs')
            else:
                base['people'].append(deepcopy(person))
    base['transactions'].sort(key=lambda row: row['id'])
    base['people'].sort(key=lambda row: row['id'])
    bindings = {'schema_version': 'whitehouse-278t-exact-source-bindings/v1', 'reports': [{
        'whitehouse_archive': whitehouse_archive, 'oge_archive': oge_archive,
        'oge_extraction': direct[1]}]}
    binding_path = review / 'whitehouse/exact-source-bindings-current.json'
    save(binding_path, bindings)
    save(root / 'base.json', base)
    save(root / 'catalog.json', catalog)
    return {'root': root, 'evidence_root': evidence, 'review_root': review,
            'base': base, 'base_path': root / 'base.json',
            'canonical_fact': canonical_fact, 'duplicate_fact': duplicate_fact,
            'canonical_facts': canonical_facts, 'duplicate_facts': duplicate_facts, 'pairs': pairs,
            'profile': profile, 'historical_wh': historical_wh,
            'final_duplicate_facts': [pair['final_duplicate_fact'] for pair in pairs],
            'historical_duplicate_facts': [pair['historical_duplicate_fact'] for pair in pairs],
            'unrelated_fact': unrelated, 'extractions': [native], 'source_bindings': bindings,
            'bindings': bindings, 'binding_path': binding_path,
            'source_sha256': sha, 'source': source, 'whitehouse_source': wh_pdf,
            'direct_audit': direct_audit,
            'oge_archive': oge_archive, 'whitehouse_archive': whitehouse_archive,
            'catalog': catalog, 'catalog_path': root / 'catalog.json',
            'audit_path': review / 'whitehouse/qualifications/candidate-current.json',
            'candidate_path': root / 'candidate.json'}
