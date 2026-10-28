"""Select complete source-backed probes for the prepared public consumer."""
import argparse
import os
from pathlib import Path


def select(args):
    import json, os
    candidate = json.load(open(args.input))
    covered = {row['ticker'] for row in candidate['security_market_data']}
    twelve = sorted(row['ticker'] for row in candidate['security_market_data']
                    if row['source_id'] == 'twelve_data_split_adjusted_eod')
    if not twelve:
        raise SystemExit('Twelve Data probe is missing from the candidate')
    person_id = args.person_id
    person_holdings = [row for row in candidate['reported_holdings']
                       if row['person_id'] == person_id]
    if not person_holdings:
        raise SystemExit('Trump person holding probe is missing')
    person_transactions = [row for row in candidate['transactions']
                           if row['person_id'] == person_id]
    if not person_transactions:
        raise SystemExit('Trump person transaction probe is missing')
    ticker = args.ticker
    if ticker not in covered or not any(
            row.get('ticker') == ticker for row in candidate['transactions']):
        raise SystemExit('MSFT transaction and market probe is missing')
    with open(args.github_output, 'a', encoding='utf-8') as output:
        output.write(f"person_id={person_id}\n")
        output.write(f"person_holding_count={len(person_holdings)}\n")
        output.write(f"person_transaction_count={len(person_transactions)}\n")
        output.write(f"ticker={ticker}\n")
        output.write(f"twelve_ticker={twelve[0]}\n")
        output.write(f"people_count={len(candidate['people'])}\n")
        output.write(f"transaction_count={len(candidate['transactions'])}\n")
        output.write(f"holding_count={len(candidate['reported_holdings'])}\n")
        output.write(f"market_row_count={len(candidate['security_market_data'])}\n")
        output.write(f"source_health_count={len(candidate['source_health'])}\n")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--person-id', required=True)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--github-output', type=Path, default=os.environ.get('GITHUB_OUTPUT'))
    args = parser.parse_args()
    if args.github_output is None: parser.error('github-output is required')
    select(args)
    return 0
