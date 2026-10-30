"""Original-bound Clerk profiles for former members absent from today's roster."""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

from .house import HouseIndexError
from .house_members import _name_key

# Lookup hints only. Every field, including the identifier, is read back from
# the official original; these identifiers cannot grant a report an identity.
BACKFILL_PROFILE_IDS = ('G000590', 'G000596', 'W000823', 'C001078', 'S001207')
MAX_BYTES = 2_000_000
BASIS = 'official_historical_profile_exact_name_state'


class _Profile(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.capture = None
        self.names, self.paragraphs, self.links = [], [], []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'a':
            self.links.append(attrs.get('href', ''))
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif 'about_bio-copy' in attrs.get('class', '').split():
                self.depth = 1
        if self.depth and tag in ('h1', 'p'):
            self.capture = (tag, [])

    def handle_data(self, data):
        if self.capture:
            self.capture[1].append(data)

    def handle_endtag(self, tag):
        if self.capture and tag == self.capture[0]:
            target = self.names if tag == 'h1' else self.paragraphs
            target.append(' '.join(''.join(self.capture[1]).split()))
            self.capture = None
        if tag == 'div' and self.depth:
            self.depth -= 1


def parse_profile(content, identifier):
    if not re.fullmatch(r'[A-Z][0-9]{6}', identifier) or len(content) > MAX_BYTES:
        raise HouseIndexError('Historical Clerk profile identity or size is invalid')
    parser = _Profile()
    parser.feed(content.decode('utf-8'))
    if (len(parser.names) != 1 or not parser.paragraphs
            or not {f'https://bioguide.congress.gov/search/bio/{identifier}',
                    f'http://bioguide.congress.gov/scripts/biodisplay.pl?index={identifier}'} & set(parser.links)):
        raise HouseIndexError('Historical Clerk profile has no unique official identity')
    match = re.fullmatch(r'[A-Za-z ]+ \(([A-Z]{2})\) [–—-] ([0-9]{1,2})(?:st|nd|rd|th), (Republican|Democrat|Independent)', parser.paragraphs[0])
    name = parser.names[0].strip('[] ').strip()
    if not match or _name_key(name) is None:
        raise HouseIndexError('Historical Clerk profile membership fields are unsupported')
    state, district, party = match.groups()
    return {'person_id': 'house:'+identifier, 'official_name': name,
            'state': state, 'state_district': state+district.zfill(2),
            'party': {'Republican':'R', 'Democrat':'D', 'Independent':'I'}[party],
            'evidence_url': f'https://bioguide.congress.gov/search/bio/{identifier}'}


def _read(root, path):
    metadata = json.loads(path.read_bytes())
    identifier = path.parent.name
    content = path.with_suffix('.html').read_bytes()
    relative = path.with_suffix('.html').relative_to(root).as_posix()
    if (metadata.get('source_url') != 'https://clerk.house.gov/Members/'+identifier
            or metadata.get('sha256') != path.stem or sha256(content).hexdigest() != path.stem
            or metadata.get('archive_path') != relative):
        raise HouseIndexError('Historical Clerk profile original binding differs')
    return parse_profile(content, identifier), metadata


def collect_backfill_profiles(root):
    """Called only inside the controlled evidence writer before qualification."""
    root = Path(root)
    errors = []
    for identifier in BACKFILL_PROFILE_IDS:
        folder = root/'house_clerk/member_profiles'/identifier
        if any(folder.glob('*.json')):
            for path in folder.glob('*.json'):
                _read(root, path)
            continue
        url = 'https://clerk.house.gov/Members/'+identifier
        request = urllib.request.Request(url, headers={'User-Agent':'unison-house-identity/0.2'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 200 or response.geturl() != url:
                    raise HouseIndexError('Historical Clerk profile response differs')
                content = response.read(MAX_BYTES+1)
            parse_profile(content, identifier)
        except (urllib.error.URLError, TimeoutError, HouseIndexError, UnicodeError) as error:
            errors.append({'source_url':url, 'reason':str(error)[:240]})
            continue
        digest = sha256(content).hexdigest()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder/(digest+'.html')
        target.write_bytes(content)
        metadata = {'source_url':url, 'sha256':digest,
                    'retrieved_at':datetime.now(timezone.utc).isoformat(),
                    'archive_path':target.relative_to(root).as_posix()}
        target.with_suffix('.json').write_text(json.dumps(metadata, sort_keys=True), encoding='utf-8')
    return errors


def suggest_historical_identity(extraction, evidence):
    source = extraction.get('source', {})
    key = _name_key(source.get('filer_name', ''))
    district = source.get('state_district', '')
    matches = {}
    for path in sorted(Path(evidence).glob('house_clerk/member_profiles/*/*.json')):
        member, metadata = _read(Path(evidence), path)
        if key is not None and _name_key(member['official_name']) == key and member['state_district'] == district:
            identifier = member['person_id']
            if identifier in matches and matches[identifier][0] != member:
                raise HouseIndexError('Historical Clerk profiles conflict')
            matches[identifier] = (member, metadata)
    if len(matches) != 1:
        return None
    member, metadata = next(iter(matches.values()))
    return {**member, 'status':'matched_automatically', 'document_id':source.get('document_id'),
            'match_basis':BASIS, 'profile_source_url':metadata['source_url'],
            'profile_sha256':metadata['sha256']}
