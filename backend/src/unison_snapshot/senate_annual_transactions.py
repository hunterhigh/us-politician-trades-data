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
INDEPENDENT_SCHEMA = "senate-efd-annual-part4b-observation/v3"
INDEPENDENT_MODE = "independent_part4b/v1"
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


def observe_part4b(metadata: dict, raw: bytes, *, independent_disclosures: bool = False) -> dict:
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
    declaration = [text for text in parsed.section_paragraphs.get(PART4B, []) if re.fullmatch(
        r"Did you, your spouse, or dependent child buy, sell, or exchange an asset "
        r"where the transaction exceeded \$1,000 and was not reported on Part 4a\? (?:Yes|No)",text)]
    return {
        "schema_version": INDEPENDENT_SCHEMA if independent_disclosures else SCHEMA, "source_id": "senate_efd",
        **({"independent_disclosure_statement":declaration[0] if len(declaration)==1 else None,
            "independent_disclosure_status":"affirmed" if len(declaration)==1 and declaration[0].endswith(" Yes") else
                "explicit_no" if explicit_no else "unknown"} if independent_disclosures else {}),
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
    if (observation.get("schema_version") not in {SCHEMA,INDEPENDENT_SCHEMA} or
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
                if (tx.get("source_id") == "senate_efd" and not str(tx.get("id","")).startswith("senate-annual-part4b:") and tx.get("person_id") == person_id and
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
                     "problems": row["problems"], "formal_transaction_id": None,
                     "source_reference": {"document_id": observation["document_id"],
                         "source_sha256": observation["source_sha256"],
                         "source_url": observation["source_url"], "section": "Part 4b",
                         "printed_row_number": row["printed_row_number"],
                         "observation_id": row["observation_id"]},
                     "candidate_disposition": "blocked_pending_relationship",
                     "candidate_blocking_reasons": sorted(set([*row["problems"],
                         "multiple_field_matches_no_official_row_link" if len(matches)>1 else
                         "field_match_without_official_row_link" if matches else
                         "no_official_ptr_row_reference_or_verified_new_disclosure"]))})
    return {"document_id": observation["document_id"], "source_sha256": observation["source_sha256"],
            "person_id": person_id, "source_report_ledger": observation["source_report_ledger"],
            "observed_row_count": len(rows), "rows": rows,
            "relationship_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
            "new_candidate_transaction_count": 0}


RELATION_RULE = "senate-annual-part4b-candidate-decisions/v1"


def materialize_independent_part4b(observation: dict, *, person_id: str, filed_at: str,
                                 data_cutoff_at: str, transactions: list[dict]) -> tuple[list[dict],dict,list[dict]]:
    """Qualify byte-replayed annual disclosure rows without deduplicating PTRs."""
    from .senate_candidate import AMOUNT_RANGES, OPEN_AMOUNTS, _TICKER
    relation = relate_part4b_to_candidate(observation,person_id=person_id,transactions=transactions)
    facts, public = [], []
    existing = {row['id']:row for row in transactions}
    for source, decision in zip(observation['rows'],relation['rows'],strict=True):
        reasons = list(source['problems'])
        if observation.get('schema_version') != INDEPENDENT_SCHEMA or observation.get('independent_disclosure_status') != 'affirmed':
            reasons.append('independent_part4b_statement_unresolved')
        owner = 'Dependent Child' if source['owner_raw']=='Child' else source['owner_raw']
        if owner not in {'Self','Joint','Spouse','Dependent Child'}:reasons.append('owner_not_supported')
        ticker = None if source['ticker_raw'] in {'','--'} else source['ticker_raw']
        if ticker is not None and not _TICKER.fullmatch(ticker):reasons.append('ticker_invalid')
        amount = AMOUNT_RANGES.get(source['amount_raw'])
        threshold = OPEN_AMOUNTS.get(source['amount_raw'])
        if amount is None and threshold is None:reasons.append('amount_range_not_supported')
        if source['transaction_type'] not in {'purchase','sale','exchange'}:reasons.append('transaction_type_not_supported')
        try:
            filed = datetime.strptime(filed_at,'%Y-%m-%d').date()
            day = datetime.strptime(source['transaction_date'],'%Y-%m-%d').date()
            if day>filed:reasons.append('transaction_after_filing')
            if filed>datetime.fromisoformat(data_cutoff_at.replace('Z','+00:00')).date():reasons.append('filing_exceeds_cutoff')
        except (ValueError,TypeError):reasons.append('filing_or_transaction_date_invalid')
        decision['cross_report_relationship']={'status':'possible_ptr_overlap' if decision['possible_ptr_transaction_ids'] else 'cross_report_relationship_unconfirmed',
            'possible_ptr_transaction_ids':decision['possible_ptr_transaction_ids'],'basis':'independent_official_part4b_disclosure_not_an_actual_trade_deduplication'}
        if reasons:
            decision.update(candidate_disposition='quarantined',candidate_blocking_reasons=sorted(set(reasons)))
            continue
        fact={'id':source['observation_id'],'filing_id':observation['document_id'],'person_id':person_id,
            'owner':owner,'asset_name':source['asset_name_raw'],'ticker':ticker,'ticker_mapping_basis':'filing_explicit' if ticker else None,
            'instrument_type':'Unspecified','option_type':None,'strike_price':None,'expiration_date':None,
            'transaction_type':source['transaction_type'],'transaction_date':source['transaction_date'],'filed_at':filed_at+'T00:00:00Z',
            'amount_low':source['amount_low'],'amount_high':source['amount_high'],
            **({'amount_low_exclusive':True} if threshold is not None else {}),
            'position_effect':'unknown','position_effect_basis':None,'source_id':'senate_efd','source':'U.S. Senate eFD',
            'source_url':observation['source_url'],'verification_status':'official_matched'}
        prior=existing.get(fact['id'])
        if prior is not None and prior!=fact:
            decision.update(candidate_disposition='quarantined',candidate_blocking_reasons=['existing_annual_formal_projection_differs'])
            continue
        facts.append(prior or fact)
        decision.update(candidate_disposition='retained_existing' if prior else 'promoted',candidate_blocking_reasons=[],formal_transaction_id=fact['id'])
        public.append({'transaction_id':fact['id'],'source_id':'senate_efd','filing_id':fact['filing_id'],
            'source_url':fact['source_url'],'source_sha256':observation['source_sha256'],'section':'Part 4b',
            'printed_row_number':source['printed_row_number'],**{key:decision['cross_report_relationship'][key]
                for key in ('status','possible_ptr_transaction_ids')}})
    relation['new_candidate_transaction_count']=sum(r['candidate_disposition']=='promoted' for r in relation['rows'])
    return facts,relation,public


def consume_part4b_candidate_decisions(annual_audit: dict, *, transactions: list[dict]) -> dict:
    """Consume the overlay's byte-verified relations without rewriting PTRs.

    The existing annual overlay reproduces exact source-copy decisions from
    archived originals. Here those report decisions receive per-row candidate
    dispositions. Unproven field overlaps remain blocked; aliases cannot add
    rows or silently replace any existing formal transaction.
    """
    from copy import deepcopy
    reports = deepcopy(annual_audit.get("annual_part4b_relationships", []))
    formal_ids = {r["id"] for r in transactions}
    by_document = {r["document_id"]:r for r in reports}
    declared = {alias:copy for copy in annual_audit.get("annual_exact_source_copies", [])
                for alias in copy["alias_document_ids"]}
    for report in reports:
        if report.get("status") == "resolved_exact_source_copy":
            proof = declared.get(report["document_id"])
            canonical = by_document.get(report.get("canonical_document_id"))
            if (not proof or proof.get("basis") != "same_verified_archived_bytes_and_report_context" or
                    proof.get("source_sha256") != report.get("source_sha256") or
                    proof.get("canonical_document_id") != report.get("canonical_document_id") or
                    canonical is None or canonical.get("source_sha256") != report.get("source_sha256") or
                    not isinstance(canonical.get("rows"),list)):
                report.update(candidate_disposition="blocked_pending_relationship",
                    candidate_blocking_reasons=["verified_canonical_part4b_rows_unavailable"])
                continue
            report["rows"] = []
            for row in canonical["rows"]:
                report["rows"].append({"observation_id":row["observation_id"],
                    "printed_row_number":row["printed_row_number"],
                    "source_reference":{"document_id":report["document_id"],
                        "source_sha256":report["source_sha256"],"section":"Part 4b",
                        "printed_row_number":row["printed_row_number"]},
                    "canonical_document_id":proof["canonical_document_id"],
                    "canonical_observation_id":row["observation_id"],
                    "candidate_disposition":"excluded_exact_source_copy",
                    "candidate_blocking_reasons":[],"formal_transaction_id":None,
                    "relation_basis":proof["basis"]})
            report["candidate_disposition"]="excluded_exact_source_copy"
        elif not isinstance(report.get("rows"),list):
            report.update(candidate_disposition="blocked_pending_relationship",
                candidate_blocking_reasons=[report.get("status", "part4b_observation_unavailable")])
        else:
            for row in report["rows"]:
                if not set(row.get("possible_ptr_transaction_ids", [])) <= formal_ids:
                    raise SenateEfdError("Annual relation references a non-current formal PTR")
                if row.get('candidate_disposition') in {'promoted','retained_existing'}:
                    if row.get('formal_transaction_id') not in formal_ids or row.get('candidate_blocking_reasons'):
                        raise SenateEfdError('Annual qualified row is not present in the actual candidate')
                elif row.get('candidate_disposition')=='quarantined':
                    if not row.get('candidate_blocking_reasons'):raise SenateEfdError('Annual quarantine has no reason')
                elif row.get("candidate_disposition") != "blocked_pending_relationship" or not row.get("candidate_blocking_reasons"):
                    raise SenateEfdError("Annual relation lacks a concrete candidate disposition")
        report["candidate_disposition_counts"] = dict(sorted(Counter(
            row["candidate_disposition"] for row in report.get("rows", [])).items()))
    return {"schema_version":"senate-annual-transaction-candidate-audit/v1",
            "rule_version":RELATION_RULE,"reports":reports,
            "candidate_disposition_counts":dict(sorted(Counter(row["candidate_disposition"]
                for report in reports for row in report.get("rows", [])).items())),
            "reports_with_unknown_row_decisions":sum(not isinstance(r.get("rows"),list) for r in reports),
            "new_candidate_transaction_count":sum(r.get('candidate_disposition')=='promoted' for report in reports for r in report.get('rows',[])),
            "formal_transactions_unchanged":not any(r.get('candidate_disposition')=='promoted' for report in reports for r in report.get('rows',[]))}
