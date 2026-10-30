from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_member_profiles import parse_profile, suggest_historical_identity, BASIS
from unison_snapshot.house_archive_queue import needs_electronic_repair, ELECTRONIC_REPAIR_VERSION

HTML = b'''<a href="https://bioguide.congress.gov/search/bio/G000590">Biography</a>
<div class="about_bio-copy"><h1>[Mark E. Green]</h1>
<p>Tennessee (TN) &ndash; 7th, Republican</p><p>Resigned: Jul. 20, 2025</p></div>'''


class HistoricalProfileTests(unittest.TestCase):
    def test_qualification_accepts_profile_digest_without_mislabeling_it_as_a_roster(self):
        from test_house_ptr import IDENTITY, META, fixture_pages
        from unison_snapshot.house_ptr import parse_word_pages, qualify_automatic
        raw = parse_word_pages(META, 'a'*64, fixture_pages(), copy_allowed=True)
        identity = {**IDENTITY, 'match_basis':BASIS, 'profile_sha256':'b'*64,
                    'profile_source_url':'https://clerk.house.gov/Members/'+IDENTITY['person_id'][6:]}
        identity.pop('roster_sha256')
        expected = qualify_automatic(raw, IDENTITY)
        actual = qualify_automatic(raw, identity)
        self.assertTrue(expected['transactions'])
        self.assertEqual(actual['transactions'], expected['transactions'])
        for changed in ({**identity,'profile_sha256':None},
                        {**identity,'profile_source_url':'https://clerk.house.gov/Members/G000590-wrong'}):
            self.assertFalse(qualify_automatic(raw,changed)['transactions'])

    def test_official_profile_fields_are_bound_to_original_identifier(self):
        member = parse_profile(HTML, 'G000590')
        self.assertEqual((member['person_id'],member['state_district'],member['party']),
                         ('house:G000590','TN07','R'))
        for changed in (HTML.replace(b'G000590',b'G000596'), HTML+HTML,
                        HTML.replace(b'7th',b'unknown'), HTML.replace(b'library',b'x').replace(b'about_bio-copy',b'x')):
            with self.assertRaises(HouseIndexError):parse_profile(changed, 'G000590')

    def test_saved_membership_fields_cannot_override_the_official_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root/'house_clerk/member_profiles/G000590'
            folder.mkdir(parents=True)
            digest = sha256(HTML).hexdigest()
            original = folder/(digest+'.html');original.write_bytes(HTML)
            metadata = {'sha256':digest, 'source_url':'https://clerk.house.gov/Members/G000590',
                        'archive_path':original.relative_to(root).as_posix(), 'party':'D'}
            path = original.with_suffix('.json');path.write_text(json.dumps(metadata))
            report = {'source':{'document_id':'test','filer_name':'Hon. Mark Dr Green','state_district':'TN07'}}
            identity = suggest_historical_identity(report, root)
            self.assertEqual(identity['match_basis'],BASIS)
            self.assertEqual(identity['party'],'R')
            report['source']['state_district'] = 'TN08'
            self.assertIsNone(suggest_historical_identity(report,root))
            original.write_bytes(HTML+b'changed')
            with self.assertRaises(HouseIndexError):suggest_historical_identity(report,root)

    def test_repair_resume_never_selects_paper_failures_other_isolation_or_2026_parser_bump(self):
        for reason in ('native_bichrome_row_visibility_unknown','identity_not_deterministic',
                       'House native contrast page budget exceeded'):
            row = {'reasons':[reason]}
            self.assertTrue(needs_electronic_repair(row))
            self.assertFalse(needs_electronic_repair({**row,'electronic_repair_version':ELECTRONIC_REPAIR_VERSION}))
        for reason in ('paper_source_fields_unverified','OCR parsing failed','amended_without_prior'):
            self.assertFalse(needs_electronic_repair({'reasons':[reason]}))

