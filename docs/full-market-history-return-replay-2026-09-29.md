# Full market history return replay — 2026-09-29

## Scope and inputs

This read-only replay checks the user-designated frontend baseline against the complete market pages referenced by its frozen `main` commit. The HTML file was not modified.

| Input | Pinned value |
|---|---|
| Latest frontend HTML | `C:\Users\admin\Downloads\politician-disclosures (3).html` |
| HTML SHA-256 | `D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4` |
| Frozen `main` commit | `958b173ca37964dfa60e4762c6ef2931e3ed06ea` |
| Frozen `market` commit | `29251969fbadb06565781aa9c9d5bafd3ce6e00e` |
| `market_pages` count | 50 unique pages |
| Ordered page-list SHA-256 (one page SHA per LF-terminated line) | `aecea230750f40e645ba7dc278d2439180c5a01341e0105e314f410e8094ef50` |
| Full-page market ticker count | 2,477 |
| HTML `security_market_data` row count | 1,748 |
| Replayed processor SHA-256 | `746e60177d65dc40504cb0bbd752a28c510627be6039e724b112ed6d46495c15` |

The HTML embeds price history clipped to its display window. For the replay, each matching HTML market row retained its other fields and received only the `price_history` array from the corresponding row in the 50 full market pages. All 1,748 HTML tickers matched. The updated processor then rebuilt the snapshot in memory. The comparison covers both return fields for every transaction: `underlying_return_since_trade` and `underlying_return_since_filing` (18,017 × 2 = 36,034 fields).

## Result

- 36,027 of 36,034 return fields exactly match the HTML value.
- Exactly 7 formerly non-null fields become `null`; there are no other differences.
- Those 7 fields are on four transaction records, all dated before their ticker's first published complete-history point:

| Transaction ID | Ticker | Trade date | Filing date | First full-history date | Changed return fields | Qualification result |
|---|---|---:|---:|---:|---|---|
| `senate-ptr:2a0ff9ca837b0e9b67b7dcc7` | TEVA | 2026-03-30 | 2026-07-21 | 2026-09-14 | since trade, since filing | ineligible: `filing_before_price_history` |
| `house-ptr:212df29f7a89bec644acfb02` | FSSL | 2025-02-07 | 2026-06-03 | 2025-11-13 | since trade | eligible; filing-date return remains available |
| `house-ptr:4d306b853f9fe2a8e9e0d3e3` | AZN | 2025-12-08 | 2026-01-13 | 2026-02-02 | since trade, since filing | ineligible: `filing_before_price_history` |
| `house-ptr:5c8d8bd901f48d8074b09da9` | AZN | 2025-12-18 | 2026-01-12 | 2026-02-02 | since trade, since filing | ineligible: `filing_before_price_history` |

For FSSL, the trade-date comparison is unavailable because the series begins after the trade; the filing date is within the series, so its filing-date return and eligibility stay intact. For TEVA and AZN, the filing dates also predate the full series and both comparisons are unavailable. Fully covered event dates retain their prior calculated values.

## Reproduction method

Run from the repository root in PowerShell with Python 3 and the pinned commits present in the local Git object database. This reads the named HTML, obtains exactly the 50 pages referenced by the frozen `main` manifest via `git show`, copies only matched full `price_history` arrays into an in-memory HTML payload, and reports the two-field comparison. It creates no files and makes no network requests.

```powershell
@'
import hashlib, json, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path('backend/src').resolve()))
from unison_snapshot.legacy import load

html_path = pathlib.Path(r'C:\Users\admin\Downloads\politician-disclosures (3).html')
raw = html_path.read_bytes()
assert hashlib.sha256(raw).hexdigest().upper() == 'D60282832DC0E38E47BE900FDD37AA386DB474DF404989467FFB8B55367EFAA4'
source = raw.decode('utf-8')
start = source.index('const DATA = ') + len('const DATA = ')
end = source.index(';\nconst PEOPLE', start)
data = json.loads(source[start:end])
main = '958b173ca37964dfa60e4762c6ef2931e3ed06ea'
market_commit = '29251969fbadb06565781aa9c9d5bafd3ce6e00e'
manifest = json.loads(subprocess.check_output(['git', 'show', f'{main}:manifest.json']))
assert manifest['market_commit'] == market_commit and len(manifest['market_pages']) == 50
assert hashlib.sha256(('\n'.join(manifest['market_pages']) + '\n').encode()).hexdigest() == 'aecea230750f40e645ba7dc278d2439180c5a01341e0105e314f410e8094ef50'
market = {}
for sha in manifest['market_pages']:
    page = json.loads(subprocess.check_output([
        'git', 'show', f'{market_commit}:market-pages/{sha}.json']))
    for row in page['security_market_data']:
        assert row['ticker'] not in market
        market[row['ticker']] = row
assert len(market) == 2477 and len(data['security_market_data']) == 1748
for row in data['security_market_data']:
    assert row['ticker'] in market
    row['price_history'] = market[row['ticker']]['price_history']
output = load('process_snapshot', version='v2').build_snapshot(data)
before = {row['id']: row for row in data['transactions']}
after = {row['id']: row for row in output['transactions']}
equal, newly_null, other = 0, [], []
for txid, row in before.items():
    for field in ('underlying_return_since_trade', 'underlying_return_since_filing'):
        old, new = row.get(field), after[txid].get(field)
        if old == new:
            equal += 1
        elif new is None and old is not None:
            newly_null.append((txid, field))
        else:
            other.append((txid, field, old, new))
print({'compared': equal + len(newly_null) + len(other), 'equal': equal,
       'newly_null': newly_null, 'other_differences': other})
assert (equal, len(newly_null), len(other)) == (36027, 7, 0)
'@ | python -
```

## Related regression coverage

`backend/tests/test_twelve_data_market.py::TwelveDataMarketTests.test_price_returns_require_an_event_date_inside_the_series` covers pre-history trade and filing dates, fully covered dates, after-cutoff filing dates, and their eligibility reasons. `backend/tests/test_producer.py::ProducerTests.test_licensed_market_requires_commit_and_is_sharded_from_entities` confirms an Alpaca-only production market uses the same pinned v2 processor. The production builder does not edit `review-input/` or the HTML baseline.
