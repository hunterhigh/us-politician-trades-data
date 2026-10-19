"""Read-only row ledger for Senate annual Part 4b transactions.

Part 4a is an electronic PTR summary. Neither section is a source of new
published transactions until amendment and cross-report relationships close.
"""
from __future__ import annotations

from datetime import datetime
from collections import Counter
import hashlib
import re

from .senate import SenateEfdError, normalize_transaction_type
from .senate_reports import _report_parser


SCHEMA = "senate-efd-annual-part4b-observation/v2"
HEADERS_4A = ("", "#", "Transaction Date", "Owner", "Ticker", "Asset Name",
              "Type", "Amount", "Comment")
HEADERS_4B = ("", "#", "Owner", "Ticker", "Asset Name", "Transaction Type",
              "Transaction Date", "Amount", "Comments")
AMOUNT = re.compile(r"\$([\d,]+)\s*-\s*\$([\d,]+)")
PART4A = "Part 4a. Periodic Transaction Report Summary"
PART4B = "Part 4b. Transactions"


def _source_ledger(source_sha: str, rows: list[dict]) -> dict:
    from .source_row_ledger import page_ledger, report_ledger

    version = "senate-annual-part4b-html-row-relations/v1"
    slots = [{"local_key": f"part4b:{row['printed_row_number']}",
              "bounds": [row["ordinal"] - 1, row["ordinal"]],
              "disposition": "data_candidate", "observation_ref": f"rows/{index}"}
             for index, row in enumerate(rows)]
    page = page_ledger(source_id="senate_efd", document_sha256=source_sha,
                       page_number=1, adapter_version=version, coverage="located",
                       slots=slots, coordinate_frame="html_part4b_table_row_ordinal")
    return report_ledger(source_id="senate_efd", document_sha256=source_sha,
                         page_count=1, adapter_version=version, pages=[page],
                         logical_rows=[{"local_key": slot["local_key"],
                            "slot_refs": [{"page_number": 1, "slot_key": slot["local_key"]}],
                            "observation_ref": slot["observation_ref"]} for slot in slots])


def observe_part4b(metadata: dict, raw: bytes) -> dict:
    """Conserve every printed Part 4b table row without qualifying a fact."""
    source_sha = hashlib.sha256(raw).hexdigest()
    if (metadata.get("schema_version") != "senate-efd-annual-archive/v1" or
            metadata.get("source_sha256") != source_sha or
            metadata.get("byte_length") != len(raw)):
        raise SenateEfdError("Senate annual transaction observation is not bound to archived bytes")
    parsed = _report_parser(raw)
    if sum(text == PART4B for tag, text in parsed.headings if tag == "h3") != 1:
        raise SenateEfdError("Senate annual Part 4b section heading is missing or ambiguous")
    part4a = [table for table in parsed.tables if table.headers == HEADERS_4A and table.section == PART4A]
    part4b = [table for table in parsed.tables if table.headers == HEADERS_4B and table.section == PART4B]
    explicit_no = any(re.fullmatch(
        r"Did you, your spouse, or dependent child buy, sell, or exchange an asset "
        r"where the transaction exceeded \$1,000 and was not reported on Part 4a\? No", text)
        for text in parsed.section_paragraphs.get(PART4B, []))
    if len(part4a) > 1 or len(part4b) > 1 or (not part4b and not explicit_no):
        raise SenateEfdError("Senate annual Part 4a/4b table is missing or ambiguous")
    if explicit_no and part4b and part4b[0].rows:
        raise SenateEfdError("Senate annual Part 4b no-transactions answer conflicts with rows")
    rows = []
    seen = set()
    for ordinal, cells in enumerate(part4b[0].rows if part4b else (), start=1):
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
        open_threshold = {"Over $1,000,000": 1000000,
                          "Over $50,000,000": 50000000}.get(raw_amount)
        if open_threshold is not None:
            low, high = open_threshold, None
        elif amount is None or low <= 0 or high < low:
            low = high = None
            problems.append("amount_range_unreadable_or_open")
        if not asset or asset == "--":
            problems.append("asset_unreadable")
        if not kind or kind == "--":
            problems.append("transaction_type_unreadable")
        try:
            normalized_type = normalize_transaction_type(kind)
        except SenateEfdError:
            normalized_type = {"transaction_type": None}
            problems.append("transaction_type_unsupported")
        rows.append({
            "observation_id": "senate-annual-part4b:" + hashlib.sha256(
                f"{source_sha}:part4b:{number}".encode()).hexdigest()[:24],
            "printed_row_number": number, "ordinal": ordinal,
            "owner_raw": owner, "ticker_raw": ticker, "asset_name_raw": asset,
            "transaction_type_raw": kind, "transaction_date_raw": raw_date,
            "transaction_type": normalized_type.get("transaction_type"),
            "transaction_date": transaction_date, "amount_raw": raw_amount,
            "amount_low": low, "amount_high": high, "comment_raw": comment,
            **({"amount_low_exclusive": True} if open_threshold is not None else {}),
            "problems": problems, "ptr_overlap_status": "unassessed",
            "disposition": "pending_relationship_and_qualification",
        })
    return {
        "schema_version": SCHEMA, "source_id": "senate_efd",
        "document_id": metadata["document_id"],
        "source_sha256": source_sha, "source_url": metadata["document_url"],
        "report_year": metadata["report_year"],
        "amendment_number": metadata["amendment_number"],
        # A missing summary table is not proof of zero PTRs and must not
        # discard a valid independent Part 4b transaction table.
        "part4a_ptr_summary_row_count": len(part4a[0].rows) if part4a else None,
        "part4a_summary_status": "table_observed" if part4a else "no_table_observed",
        "part4b_printed_row_count": len(rows), "rows": rows,
        "section_disposition": "explicit_no_transactions" if explicit_no else "table_observed",
        "source_report_ledger": _source_ledger(source_sha, rows),
        "production_eligible_row_count": 0,
    }


def relate_part4b_to_candidate(observation: dict, *, person_id: str,
                               transactions: list[dict]) -> dict:
    """Expose exact possible PTR overlaps without merging disclosure rows.

    An annual row says it was not included in the electronic Part 4a summary.
    That alone cannot establish its relationship to a separately filed paper
    PTR or to two equal rows under different accounts. Even a sole field match
    is retained as a possible relation, never an automatic duplicate decision.
    """
    if (observation.get("schema_version") != SCHEMA or
            observation.get("source_report_ledger") != _source_ledger(
                observation["source_sha256"], observation["rows"]) or
            observation.get("part4b_printed_row_count") != len(observation["rows"])):
        raise SenateEfdError("Senate annual Part 4b relation input is inconsistent")
    rows = []
    for row in observation["rows"]:
        expected_id = "senate-annual-part4b:" + hashlib.sha256(
            f"{observation['source_sha256']}:part4b:{row['printed_row_number']}".encode()).hexdigest()[:24]
        if row.get("observation_id") != expected_id:
            raise SenateEfdError("Senate annual Part 4b observation identity changed")
        owner = "Dependent Child" if row["owner_raw"] == "Child" else row["owner_raw"]
        ticker = None if row["ticker_raw"] in {"", "--"} else row["ticker_raw"]
        matches = []
        if not row["problems"]:
            for tx in transactions:
                if (tx.get("source_id") == "senate_efd" and tx.get("person_id") == person_id and
                        tx.get("owner") == owner and tx.get("ticker") == ticker and
                        tx.get("asset_name") == row["asset_name_raw"] and
                        tx.get("transaction_date") == row["transaction_date"] and
                        tx.get("transaction_type") == row["transaction_type"] and
                        tx.get("amount_low") == row["amount_low"] and
                        tx.get("amount_high") == row["amount_high"] and
                        tx.get("amount_low_exclusive") == row.get("amount_low_exclusive")):
                    matches.append(tx["id"])
        status = ("field_problems_unresolved" if row["problems"] else
                  "possible_ptr_overlap" if matches else "ptr_relationship_unresolved")
        rows.append({"observation_id": row["observation_id"],
                     "printed_row_number": row["printed_row_number"],
                     "status": status, "possible_ptr_transaction_ids": sorted(matches),
                     "problems": row["problems"], "formal_transaction_id": None})
    return {"document_id": observation["document_id"], "source_sha256": observation["source_sha256"],
            "person_id": person_id, "source_report_ledger": observation["source_report_ledger"],
            "observed_row_count": len(rows), "rows": rows,
            "relationship_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
            "new_candidate_transaction_count": 0}
