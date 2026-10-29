"""Shared OGE identity, formal row projection and legacy owner evidence."""
from copy import deepcopy
import hashlib
import json
import re

from .oge import OgeCatalogError
from .oge_reports import PARSER_VERSION

def _identity_key(record: dict) -> tuple[str, str, str]:
    values = []
    for field in ("filer_name", "agency", "position_title"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise OgeCatalogError(f"OGE identity has invalid {field}")
        values.append(" ".join(value.casefold().replace(",", " ").split()))
    return tuple(values)  # type: ignore[return-value]


def _person_id(key: tuple[str, str, str]) -> str:
    return "oge:" + hashlib.sha256("|".join(key).encode()).hexdigest()[:16]


def _instrument(asset_name: str, ticker: str | None) -> str:
    value = asset_name.casefold()
    if " etf" in value or value.endswith("etf"):
        return "ETF"
    if "mutual fund" in value or "fund" in value:
        return "Mutual Fund"
    # A disclosure's "See Endnote" callout is not a debt instrument.
    if "bond" in value or "treasury" in value or re.search(r"\bnotes?\b", value):
        return "Bond"
    if ticker or "stock" in value or "inc." in value or "corp." in value:
        return "Stock"
    return "Unspecified"


def _transaction_projection(extraction: dict, row: dict, person_id: str) -> dict:
    """One formal projection for first publication and strict source-bound repeat."""
    from .oge_transaction_adapter import open_amount_qualified
    asset_name, ticker = row["asset_name"], row["ticker"]
    is_open = open_amount_qualified(row, extraction["parser_version"])
    return {
        "id": row["extraction_id"],
        "filing_id": extraction["document_id"],
        "person_id": person_id,
        "owner": row["owner"],
        "asset_name": f"{asset_name} — {row['account_title']}" if row.get("account_title") else asset_name,
        "ticker": ticker,
        "ticker_mapping_basis": "filing_explicit" if ticker else None,
        "instrument_type": _instrument(asset_name, ticker),
        "option_type": None,
        "strike_price": None,
        "expiration_date": None,
        "transaction_type": row["transaction_type"],
        "transaction_date": row["transaction_date"],
        "filed_at": f"{extraction['filed_at']}T00:00:00Z",
        "amount_low": row["amount_low"],
        "amount_high": row["amount_high"],
        **({"amount_low_exclusive": True} if is_open else {}),
        "position_effect": "unknown",
        "position_effect_basis": None,
        "source_id": "oge",
        "source": "U.S. Office of Government Ethics",
        "source_url": extraction["source_url"],
        "verification_status": "official_matched",
    }


def _legacy_default_owner_evidence(legacy: dict, old: dict) -> dict | None:
    """Distinguish the v2 default from an explicit owner using its whole input."""
    from .oge_reports import parse_table_rows, _ACCOUNT_HEADING
    from .oge_transaction_adapter import _row_key, _cells
    if (legacy.get("parser_version") != PARSER_VERSION or old.get("owner") != "Self"
            or old.get("owner_observation_reference") or old.get("owner_disclosure_basis")):
        return None
    inventory = legacy.get("source_rows")
    if not isinstance(inventory, list):
        return None
    physical = [(row["page_number"], _cells(row["cells"])) for row in inventory]
    locators = [index for index, (page, cells) in enumerate(physical)
                if _row_key({"page_number": page, "cells": cells}) == _row_key(old)]
    if len(locators) != 1:
        return None
    # A malformed preceding label does not prove absence of explicit scope.
    for _, cells in physical[:locators[0]]:
        if (len(cells) == 6 and cells[1] and not any((cells[2], cells[3], cells[5]))
                and not _ACCOUNT_HEADING.fullmatch(cells[1])
                and not re.fullmatch(r"(?:filer|self|spouse|dependent child(?:ren)?)(?:'s)? (?:assets|accounts?)", cells[1], re.I)
                and "intentionally" not in cells[1].casefold()):
            return None
    replays = []
    for default in ("Self", "Undisclosed"):
        good, bad = parse_table_rows(physical, source_sha=legacy["source_sha256"], default_owner=default)
        matches = [row for row in good + bad if _row_key(row) == _row_key(old)]
        if len(matches) != 1:
            return None
        replays.append(matches[0])
    fields = ("asset_name", "ticker", "transaction_type", "transaction_date", "amount_low", "amount_high")
    if (replays[0]["owner"] != "Self" or replays[1]["owner"] != "Undisclosed"
            or any(replays[0].get(field) != old.get(field) for field in fields)):
        return None
    return {"source_sha256": legacy["source_sha256"], "document_id": legacy["document_id"],
            "legacy_parser_version": PARSER_VERSION, "source_rows_index": locators[0],
            "page_number": old["page_number"], "cells": deepcopy(old["cells"]),
            "source_rows_sha256": hashlib.sha256(json.dumps(inventory, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "basis": "whole_saved_source_input_replayed_with_two_defaults_no_explicit_owner"}
