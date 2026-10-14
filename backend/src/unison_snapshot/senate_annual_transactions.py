"""Read-only row ledger for Senate annual Part 4b transactions.

Part 4a is an electronic PTR summary. Neither section is a source of new
published transactions until amendment and cross-report relationships close.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re

from .senate import SenateEfdError
from .senate_reports import _report_parser


SCHEMA = "senate-efd-annual-part4b-observation/v1"
HEADERS_4A = ("", "#", "Transaction Date", "Owner", "Ticker", "Asset Name",
              "Type", "Amount", "Comment")
HEADERS_4B = ("", "#", "Owner", "Ticker", "Asset Name", "Transaction Type",
              "Transaction Date", "Amount", "Comments")
AMOUNT = re.compile(r"\$([\d,]+)\s*-\s*\$([\d,]+)")


def observe_part4b(metadata: dict, raw: bytes) -> dict:
    """Conserve every printed Part 4b table row without qualifying a fact."""
    source_sha = hashlib.sha256(raw).hexdigest()
    if (metadata.get("schema_version") != "senate-efd-annual-archive/v1" or
            metadata.get("source_sha256") != source_sha or
            metadata.get("byte_length") != len(raw)):
        raise SenateEfdError("Senate annual transaction observation is not bound to archived bytes")
    parsed = _report_parser(raw)
    if sum("Part 4b. Transactions" in text for text in parsed.text_segments) != 1:
        raise SenateEfdError("Senate annual Part 4b section heading is missing or ambiguous")
    part4a = [table for table in parsed.tables if table.headers == HEADERS_4A]
    part4b = [table for table in parsed.tables if table.headers == HEADERS_4B]
    if len(part4a) != 1 or len(part4b) != 1:
        raise SenateEfdError("Senate annual Part 4a/4b table is missing or ambiguous")
    rows = []
    seen = set()
    for ordinal, cells in enumerate(part4b[0].rows, start=1):
        if len(cells) != len(HEADERS_4B):
            raise SenateEfdError("Senate annual Part 4b row changed shape")
        number = cells[1]
        if not re.fullmatch(r"[1-9]\d*", number) or number in seen:
            raise SenateEfdError("Senate annual Part 4b printed row numbers are not unique")
        seen.add(number)
        owner, ticker, asset, kind, raw_date, raw_amount, comment = cells[2:]
        problems = []
        try:
            transaction_date = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            transaction_date = None
            problems.append("transaction_date_unreadable")
        amount = AMOUNT.fullmatch(raw_amount)
        low = int(amount[1].replace(",", "")) if amount else None
        high = int(amount[2].replace(",", "")) if amount else None
        if amount is None or low <= 0 or high < low:
            low = high = None
            problems.append("amount_range_unreadable_or_open")
        if not asset or asset == "--":
            problems.append("asset_unreadable")
        if not kind or kind == "--":
            problems.append("transaction_type_unreadable")
        rows.append({
            "observation_id": "senate-annual-part4b:" + hashlib.sha256(
                f"{source_sha}:part4b:{number}".encode()).hexdigest()[:24],
            "printed_row_number": number, "ordinal": ordinal,
            "owner_raw": owner, "ticker_raw": ticker, "asset_name_raw": asset,
            "transaction_type_raw": kind, "transaction_date_raw": raw_date,
            "transaction_date": transaction_date, "amount_raw": raw_amount,
            "amount_low": low, "amount_high": high, "comment_raw": comment,
            "problems": problems, "ptr_overlap_status": "unassessed",
            "disposition": "pending_relationship_and_qualification",
        })
    return {
        "schema_version": SCHEMA, "source_id": "senate_efd",
        "document_id": metadata["document_id"],
        "source_sha256": source_sha, "source_url": metadata["document_url"],
        "report_year": metadata["report_year"],
        "amendment_number": metadata["amendment_number"],
        "part4a_ptr_summary_row_count": len(part4a[0].rows),
        "part4b_printed_row_count": len(rows), "rows": rows,
        "production_eligible_row_count": 0,
    }
