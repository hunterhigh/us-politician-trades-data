"""Existing browser-suite fixtures, moved from the retired backend unit modules."""
import json
from pathlib import Path
from unison_snapshot.codec import encode
from unison_snapshot.public_repo import PublicSnapshotError
FIXTURE = Path(__file__).resolve().parents[2] / 'backend/examples/synthetic.json'
COMMIT = '1' * 40

class FakeTransport:

    def __init__(self, files, commit=COMMIT, extra_commits=None):
        self.files = files
        self.commit = commit
        self.files_by_commit = {commit: files, **(extra_commits or {})}
        self.calls = []

    def get(self, url, limit, *, api=False):
        self.calls.append((url, limit, api))
        if api:
            reference = {'type': 'commit', 'sha': self.commit}
            value = encode({'object': reference})
        else:
            matched = next(((sha, files) for sha, files in self.files_by_commit.items() if f'/{sha}/' in url), None)
            if matched is None:
                raise AssertionError(url)
            sha, files = matched
            value = files[url.split(f'/{sha}/', 1)[1]]
        if len(value) > limit:
            raise PublicSnapshotError('Repository object exceeds the contract size limit')
        return value

def production_payload():
    data = json.loads(FIXTURE.read_text(encoding='utf-8'))
    data['meta']['is_demo'] = False
    hosts = {'house_clerk': 'disclosures-clerk.house.gov', 'senate_efd': 'efdsearch.senate.gov'}
    for row in data['transactions'] + data['reported_holdings']:
        row['verification_status'] = 'official_matched'
        row['source_url'] = f"https://{hosts[row['source_id']]}/test-contract/{row['filing_id']}"
    for row in data['source_health']:
        row['status'] = 'disabled' if row['source_id'] == 'alpaca_sip_eod' else 'ok'
    return data
