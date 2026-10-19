"""House paper observations and opt-in source-bound fact interpretation.

The legacy observation entry retains its pending contract. Saved interpretation
checks source crops and field conflicts before calling existing qualification.
"""

from __future__ import annotations

import re

from .house_ptr import OWNER_CODES, QUALIFICATION_SCHEMA
from .source_row_ledger import page_ledger, report_ledger
from .house_ptr_amount_header import evaluate_amount_header_pages
from .house_ptr_key_fields_shadow import read_house_ptr_key_fields
from .house_ptr_section_context import relate_house_ptr_sections
from .house_ptr_duplicate_observation import observe_repeated_trade_signatures
from .house_ptr_paper_temporal import (PARSER_VERSION, audit_paper_temporal_relations,
                                      validate_paper_source_metadata)


def _pending_transaction_projection(result: dict, metadata: dict) -> dict:
    """Shape observed paper slots for the builder without assigning formal IDs."""
    document_id, source_sha, _, _ = validate_paper_source_metadata(metadata)
    if result["document_id"] != document_id or result["source_sha256"] != source_sha:
        raise ValueError("House paper projection source does not match qualification")
    projected = []
    for row in result["quarantined"]:
        fields = row.get("raw_fields")
        if not isinstance(fields, dict):
            continue
        amount = row.get("amount_range_observation")
        projected.append({
            "observation_key": row["observation_key"],
            "evidence": row["evidence"],
            "person_id": result["identity"].get("person_id"),
            "filing_id": document_id,
            "source_url": metadata["source_url"],
            "filed_at": f"{metadata['filed_date']}T00:00:00Z",
            "owner": OWNER_CODES.get(fields.get("owner_code")),
            "asset_name": fields.get("asset_raw") or None,
            "transaction_date": fields.get("event_date"),
            "transaction_type": fields.get("direction_mark"),
            "amount_letter": fields.get("amount_letter"),
            "amount_low": amount.get("low") if isinstance(amount, dict) else None,
            "amount_high": amount.get("high") if isinstance(amount, dict) else None,
            "amount_kind": amount.get("kind") if isinstance(amount, dict) else None,
            "field_status": {
                "owner": "ocr_unverified" if fields.get("owner_code") else "unreadable",
                "asset_name": "ocr_unverified" if fields.get("asset_raw") else "unreadable",
                "transaction_date": fields.get("event_date_status"),
                "transaction_type": fields.get("direction_status"),
                "amount": "printed_bounds_unverified_transaction" if amount else
                          fields.get("amount_status", "unresolved"),
            },
            "source_quality_flags": row.get("source_quality_flags", []),
            "blocking_reasons": row["reasons"],
        })
    return {"schema_version": "house-paper-pending-transaction-projection/v1",
            "status": "unverified_pending", "source_sha256": source_sha,
            "document_id": document_id, "row_count": len(projected),
            "rows": projected}


def qualify_paper_observation(observation: dict, metadata: dict,
                              identity: dict, *, amount_header: dict | None = None,
                              peer_metadata: list[dict] | None = None) -> dict:
    """Account for every physical row while blocking unverified promotion."""
    source_sha = observation.get("source_sha256")
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha)
            or metadata.get("sha256") != source_sha):
        raise ValueError("House paper observation and archived source hashes differ")
    document_id = metadata.get("document_id")
    if (not isinstance(document_id, str) or not document_id
            or identity.get("document_id") != document_id):
        raise ValueError("House paper identity does not match the archived document")
    header_ranges = None
    header_evidence = None
    if amount_header is not None:
        if (amount_header.get("source_sha256") != source_sha or
                amount_header.get("page_count") != len(observation["pages"])):
            raise ValueError("House paper amount header does not match the observation")
        checked = evaluate_amount_header_pages(source_sha, amount_header.get("pages"))
        if (checked["status"] != amount_header.get("status") or
                checked["ranges"] != amount_header.get("ranges")):
            raise ValueError("House paper amount header interpretation changed")
        header_ranges = checked["ranges"]
        header_evidence = checked
    reading = read_house_ptr_key_fields(observation, filing_year=metadata.get("filing_year"))
    section_context = relate_house_ptr_sections(observation)
    duplicates = observe_repeated_trade_signatures(reading, section_context)
    repeated_by_slot = {}
    for group in duplicates["groups"]:
        slots = [(item["page"], item["physical_band_index"])
                 for item in group["slots"]]
        for slot in slots:
            repeated_by_slot[slot] = [
                {"page": other[0], "physical_band_index": other[1]}
                for other in slots if other != slot]
    read_by_slot = {(row["page"], row["physical_band_index"]): row
                    for row in reading["rows"]}
    section_by_slot = {(row["page"], row["physical_band_index"]): row
                       for row in section_context["data_rows"]}
    heading_by_slot = {(row["page"], row["physical_band_index"]): row
                       for row in section_context["sections"]}
    if len(read_by_slot) != reading["data_candidate_count"]:
        raise ValueError("House paper reading has duplicate physical slots")
    quarantined = []
    structural_observations = []
    physical_counts = {"data_candidate": 0, "unknown": 0, "blank": 0}
    seen = set()
    for page in observation["pages"]:
        page_number = page.get("page")
        for physical in page["rows"]:
            band_index = physical.get("physical_band_index")
            slot = (page_number, band_index)
            if (type(page_number) is not int or type(band_index) is not int
                    or page_number < 1 or band_index < 1 or slot in seen):
                raise ValueError("House paper physical slot is invalid or duplicated")
            seen.add(slot)
            disposition = physical.get("disposition")
            # Geometry alone cannot prove a short first band is an example.
            # Keep it unresolved until source-bound semantic classification.
            if disposition == "title_legend":
                disposition = "unknown"
            if disposition not in physical_counts:
                raise ValueError("House paper physical disposition is unknown")
            physical_counts[disposition] += 1
            if disposition == "blank":
                continue
            if slot in heading_by_slot:
                structural_observations.append(heading_by_slot[slot])
                continue
            key = f"paper-slot:{source_sha}:{page_number}:{band_index}"
            reasons = (["paper_source_fields_unverified"] if disposition == "data_candidate"
                       else ["physical_row_disposition_unresolved"])
            read = read_by_slot.get(slot)
            if disposition == "data_candidate":
                if read is None:
                    raise ValueError("House paper data slot has no key-field reading")
                reasons.extend(read["blocking_reasons"])
            elif read is not None:
                raise ValueError("House paper non-data slot has a key-field reading")
            amount_observation = None
            if read and read["fields"] and header_ranges:
                fields = read["fields"]
                spouse_mark = fields["spouse_dc_over_1000000_mark"]
                letter = fields["amount_letter"]
                if spouse_mark is True and letter is None:
                    letter = "K"
                elif spouse_mark is True:
                    letter = None
                if letter in header_ranges:
                    amount_observation = {
                        "status": "printed_bounds_unverified_transaction",
                        "letter": letter, **header_ranges[letter],
                        "header_source_sha256": source_sha,
                    }
            quarantined.append({
                "extraction_id": key, "observation_key": key,
                "reasons": sorted(set(reasons)), "source_sha256": source_sha,
                "evidence": {"page": page_number, "physical_band_index": band_index,
                             "band_normalized": physical.get("band")},
                "raw_fields": read["fields"] if read else None,
                "source_field_evidence": read.get("source_field_evidence") if read else None,
                "source_quality_flags": read.get("source_quality_flags", []) if read else [],
                "account_section_observation": section_by_slot.get(slot),
                "possible_repeated_with": repeated_by_slot.get(slot, []),
                "amount_range_observation": amount_observation,
            })
    if physical_counts["data_candidate"] != reading["data_candidate_count"]:
        raise ValueError("House paper physical census and key-field reading differ")
    if (len(quarantined) + len(structural_observations) + physical_counts["blank"]
            != section_context["physical_slot_count"]):
        raise ValueError("House paper section and qualification census differ")
    if not quarantined:
        raise ValueError("House paper has no observed nonblank rows")
    document_reasons = ["paper_source_fields_unverified"]
    if section_context["unclassified_unknown_count"]:
        document_reasons.append("physical_row_disposition_unresolved")
    result = {
        "schema_version": QUALIFICATION_SCHEMA,
        "source_sha256": source_sha, "parser_version": PARSER_VERSION,
        "document_id": document_id, "identity": identity,
        "document_disposition": {"status": "paper_observation_unverified",
                                 "physical_counts": physical_counts,
                                 "section_heading_count": len(structural_observations),
                                 "amount_header_status": amount_header.get("status")
                                 if amount_header else "not_observed"},
        "amount_header_observation": header_evidence,
        "section_context_observation": section_context,
        "duplicate_observation": duplicates,
        "structural_observations": structural_observations,
        "transactions": [], "quarantined": quarantined, "advisories": [],
        "qualification": {
            "method": "paper_observation_blocked",
            "status": "quarantined_rows", "qualified_count": 0,
            "quarantined_count": len(quarantined), "advisory_count": 0,
            "zero_transaction_document_count": 0,
            "document_reasons": document_reasons, "production_eligible": False,
        },
    }
    pages = []
    logical = []
    for page in observation["pages"]:
        slots = []
        for physical in page["rows"]:
            slot = (page["page"], physical["physical_band_index"])
            state = "heading" if slot in heading_by_slot else physical["disposition"]
            if state == "title_legend":
                state = "unknown"
            key = str(physical["physical_band_index"])
            slots.append({"local_key": key, "bounds": physical["band"],
                          "disposition": state, "observation_ref": f"pages/{page['page']}/bands/{key}"})
            if state == "data_candidate":
                logical.append({"local_key": f"p{page['page']}:band{key}",
                                "slot_refs": [{"page_number": page["page"], "slot_key": key}]})
        located = page.get("region_status") == "located_shadow"
        pages.append(page_ledger(source_id="house_clerk", document_sha256=source_sha,
            adapter_version=PARSER_VERSION, page_number=page["page"],
            coverage="located" if located else "unknown", slots=slots,
            coverage_reason=None if located else "paper_grid_coverage_unresolved",
            coordinate_frame="normalized_source_page"))
    result["source_report_ledger"] = report_ledger(source_id="house_clerk",
        document_sha256=source_sha, adapter_version=PARSER_VERSION, page_count=len(pages),
        pages=pages, logical_rows=logical)
    result["pending_transaction_projection"] = _pending_transaction_projection(result, metadata)
    if peer_metadata is not None:
        result["temporal_relation_observation"] = audit_paper_temporal_relations(
            result, metadata, peer_metadata)
    return result


FACT_PARSER_VERSION = "house-paper-source-bound-qualification-v2"


def _source_bound_no_transactions(pdf_bytes: bytes, observation: dict) -> dict | None:
    """Prove an explicit statement on every complete physical source page.

    Geometry is recomputed without OCR so omitted bands/pages cannot turn a
    partial observation into a report-level zero. Empty OCR alone never counts.
    """
    import io
    import pdfplumber
    from .house_ptr_region_shadow import locate_transaction_grid
    from .house_ptr_cell_observation_shadow import _column_boxes
    from .house_ptr_fixed_source_core import check_source_cells

    pages = observation.get("pages", [])
    if not pages or any(page.get("region_status") != "located_shadow" or
                        page.get("cell_status") != "raw_observed_unverified" or
                        page.get("coordinate_frame") != "original_page" for page in pages):
        return None
    statement_pattern = (r"(?:Nothing to report|No transactions? to report)"
                         r"(?: for [A-Za-z]+ 20\d{2})?\.?")
    if not any(row.get("disposition") == "unknown" and re.fullmatch(statement_pattern,
               str(((row.get("cells") or {}).get("asset") or {}).get("raw_text", "")).strip(), re.I)
               for page in pages for row in page.get("rows", [])):
        return None
    import numpy as np
    check_source_cells(pdf_bytes, observation, page_count=len(pages),
                       allow_unobserved=False, verify_crop_hashes=True)
    statements = []
    ledgers = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page, source in zip(pages, pdf.pages):
            image = source.to_image(resolution=200).original
            grid = locate_transaction_grid(image)
            columns = _column_boxes(grid, image)
            if columns.get("status") != "located_shadow":
                return None
            physical = grid.get("physical_row_bands", [])
            rows = page["rows"]
            if len(rows) != len(physical) or not rows:
                return None
            slots = []
            page_statements = []
            for index, (row, band) in enumerate(zip(rows, physical), 1):
                if (row["physical_band_index"] != index or row["band"] != band["band"] or
                        row["disposition"] != band["disposition"]):
                    return None
                cells = row["cells"]
                if not isinstance(cells, dict):
                    return None
                expected = [(cells[name], columns[column]) for name, column in (
                    ("owner", "owner"), ("asset", "asset"),
                    ("event_date", "event"), ("notification_date", "notification"))]
                for name, column in (("direction", "action"), ("amount", "amount")):
                    bounds = list(zip(columns[column], columns[column][1:]))
                    if len(cells[name]) != len(bounds):
                        return None
                    expected.extend(zip(cells[name], bounds))
                for cell, (left, right) in expected:
                    top, bottom = band["band"]
                    if (cell.get("bbox_normalized") != [left, top, right, bottom] or
                            cell.get("bbox_pixels") != [int(left * image.width), int(top * image.height),
                                                        int(right * image.width), int(bottom * image.height)]):
                        return None
                asset = cells["asset"].get("raw_text", "").strip()
                others = [cells[k] for k in ("owner", "event_date", "notification_date")]
                others += cells["direction"] + cells["amount"]
                def empty(cell):
                    if cell.get("raw_text", "").strip():
                        return False
                    pixels = np.asarray(image.crop(tuple(cell["bbox_pixels"])).convert("L"))
                    py, px = max(1, pixels.shape[0] // 6), max(1, pixels.shape[1] // 6)
                    interior = pixels[py:-py, px:-px]
                    return bool(interior.size and not (interior < 100).any())
                state = row["disposition"]
                if state == "title_legend" and index == 1 and re.fullmatch(
                        r"Example\s+Mega\s+Corp\s+Common\s+Stock", asset, re.I):
                    disposition = "note"
                elif state == "unknown" and all(empty(cell) for cell in others) and re.fullmatch(
                        statement_pattern, asset, re.I):
                    page_statements.append({"page": page["page"], "physical_band_index": index,
                                            "statement": asset})
                    disposition = "note"
                elif state == "blank" and all(empty(cell) for cell in [cells["asset"], *others]):
                    disposition = "blank"
                else:
                    return None
                slots.append({"local_key": str(index), "bounds": row["band"],
                              "disposition": disposition})
            if len(page_statements) != 1:
                return None
            statements.extend(page_statements)
            ledgers.append(page_ledger(source_id="house_clerk",
                document_sha256=observation["source_sha256"], adapter_version=FACT_PARSER_VERSION,
                page_number=page["page"], coverage="located", slots=slots,
                coordinate_frame="normalized_source_page"))
    return {"declaration": {"status": "explicit_no_transactions", "statements": statements,
                            "method": "complete_source_bound_paper_statement/v1"},
            "ledger": report_ledger(source_id="house_clerk",
                document_sha256=observation["source_sha256"], adapter_version=FACT_PARSER_VERSION,
                page_count=len(pages), pages=ledgers, logical_rows=[])}


def materialize_paper_observation(pdf_bytes: bytes, observation: dict, metadata: dict,
                                  identity: dict, *, amount_header: dict | None = None,
                                  field_reread: dict | None = None,
                                  prior_qualification: dict | None = None) -> dict:
    """Interpret saved source cells through existing House qualification.

    This opt-in development entry never changes prior qualified paper facts.
    Unknown cover status, conflicting readings, and incomplete printed legends
    remain specific row blockers. It does not rerun OCR or invent a ticker/name.
    """
    from copy import deepcopy
    import hashlib
    import io
    import json
    from .house_ptr import SCHEMA, qualify_automatic
    from .house_ptr_fixed_source_core import check_source_cells, paper_disclosure_id

    document_id, source_sha, _, _ = validate_paper_source_metadata(metadata)
    if hashlib.sha256(pdf_bytes).hexdigest() != source_sha or observation.get("source_sha256") != source_sha:
        raise ValueError("House paper materialization source mismatch")
    if prior_qualification is not None:
        if (prior_qualification.get("source_sha256") != source_sha or
                prior_qualification.get("document_id") != document_id):
            raise ValueError("House prior qualification source mismatch")
        if prior_qualification.get("transactions"):
            return deepcopy(prior_qualification)
    zero = _source_bound_no_transactions(pdf_bytes, observation)
    if zero is not None:
        extraction = {"schema_version": SCHEMA, "parser_version": FACT_PARSER_VERSION,
            "source_sha256": source_sha, "source": metadata,
            "extraction_method": "source_bound_paper_cells", "transactions": [],
            "document_disposition": zero["declaration"], "source_report_ledger": zero["ledger"]}
        result = qualify_automatic(extraction, identity)
        result["materialized_extraction"] = extraction
        result["observation_materialization"] = {"source_sha256": source_sha,
            "observation_sha256": hashlib.sha256(json.dumps(observation, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "observed_data_row_count": 0, "unresolved_physical_row_count": 0,
            "production_mutation_authorized": False}
        return result
    baseline = qualify_paper_observation(observation, metadata, identity, amount_header=amount_header)
    source_slots = check_source_cells(pdf_bytes, observation,
        page_count=len(observation["pages"]), allow_unobserved=True, verify_crop_hashes=True)
    # New printed-frame measurements are hints until recomputed from the
    # already hash-bound source crop. A changed hint cannot erase a real mark.
    from .house_ptr_cell_observation_shadow import _printed_checkbox
    box_pages = {}
    for (page, _), physical in source_slots.items():
        cells = physical.get("cells") or {}
        for cell in cells.get("direction", []) + cells.get("amount", []):
            if "printed_checkbox" not in cell:
                continue
            if page not in box_pages:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    box_pages[page] = pdf.pages[page - 1].to_image(resolution=200).original
            actual = _printed_checkbox(box_pages[page].crop(tuple(cell["bbox_pixels"])))
            if actual != cell["printed_checkbox"]:
                raise ValueError("House paper printed checkbox differs from bound PDF")
    second = {}
    if field_reread is not None:
        if (field_reread.get("schema_version") != "house-paper-key-field-reread/v1" or
                field_reread.get("source_sha256") != source_sha or
                field_reread.get("qualification_status") != "unverified_shadow_only" or
                not isinstance(field_reread.get("rereads"), list)):
            raise ValueError("House paper secondary readings source mismatch")
        for item in field_reread["rereads"]:
            key = (item.get("page"), item.get("physical_band_index"), item.get("field"))
            if key in second:
                raise ValueError("House paper secondary reading duplicated")
            second[key] = item
    cover = observation["pages"][0].get("filing_status_observation")
    extraction_rows = []
    unresolved_physical = []
    rendered_secondary = {}

    for pending in baseline["quarantined"]:
        fields = pending.get("raw_fields")
        evidence = pending["evidence"]
        page, band = evidence["page"], evidence["physical_band_index"]
        if not isinstance(fields, dict):
            unresolved_physical.append(pending)
            continue
        physical = source_slots.get((page, band))
        blockers = [reason for reason in pending["reasons"] if reason != "paper_source_fields_unverified"]
        if cover != "New":
            blockers.append("filing_status_unobserved" if cover is None else "revision_relationship_unresolved")
        asset = fields.get("asset_raw") or ""
        cell = physical["cells"]["asset"] if physical and physical.get("cells") else None
        reread = second.get((page, band, "asset"))
        if cell is None or reread is None:
            blockers.append("asset_reading_not_corroborated")
        elif not _asset_reading_is_bound(pdf_bytes, reread, cell, page, rendered_secondary):
            blockers.append("asset_reading_evidence_unbound")
        elif (re.sub(r"[^a-z0-9]", "", asset.casefold()) !=
              re.sub(r"[^a-z0-9]", "", str(reread.get("second_ocr_raw", "")).casefold())):
            blockers.append("asset_reading_conflict")
        if any(flag in {"owner_ocr_confidence_below_70", "event_date_ocr_confidence_below_70"}
               for flag in pending.get("source_quality_flags", [])):
            blockers.append("key_source_reading_uncertain")
        context = pending.get("account_section_observation") or {}
        if context.get("section_key"):
            blockers.append("account_title_reading_not_corroborated")
        amount = pending.get("amount_range_observation")
        if not isinstance(amount, dict) and amount_header and amount_header.get("reading_mode") == "printed_columns/v1":
            readings = [item for item in amount_header.get("column_readings", [])
                        if item.get("page") == page and item.get("label") == fields.get("amount_letter")]
            if len(readings) == 1 and physical and physical.get("cells"):
                amount = _bound_amount_column(pdf_bytes, readings[0], physical, page, rendered_secondary)
        if not isinstance(amount, dict):
            blockers.append("amount_header_unresolved")
            amount = {}
        low, high = amount.get("low"), amount.get("high")
        exclusive = None
        if high is None and type(low) is int:
            letter = amount.get("letter")
            if letter == "J":
                low, exclusive = 50000000, True
            elif letter == "K" and OWNER_CODES.get(fields.get("owner_code")) in {"Spouse", "Dependent Child"}:
                low, exclusive = 1000000, True
                blockers = [reason for reason in blockers if reason not in {
                    "amount_mark_unresolved", "spouse_dc_open_ended_amount_not_representable"}]
        extraction_rows.append({
            "extraction_id": paper_disclosure_id(source_sha, page, band),
            "filing_status": cover, "reported_transaction_id": None,
            "owner": OWNER_CODES.get(fields.get("owner_code")), "asset_name": asset,
            "ticker": None, "instrument_type": None,
            "transaction_type": fields.get("direction_mark"),
            "transaction_date": fields.get("event_date"),
            "notification_date": fields.get("notification_date"),
            "amount_low": low, "amount_high": high,
            "amount_kind": "open_ended" if exclusive is not None else "range",
            **({"amount_low_exclusive": exclusive} if exclusive is not None else {}),
            "field_problems": sorted(set(blockers)), "evidence": evidence,
        })
    extraction = {"schema_version": SCHEMA, "parser_version": FACT_PARSER_VERSION,
        "source_sha256": source_sha, "source": metadata,
        "extraction_method": "source_bound_paper_cells", "transactions": extraction_rows,
        "source_report_ledger": baseline["source_report_ledger"]}
    result = qualify_automatic(extraction, identity)
    result["quarantined"].extend(unresolved_physical)
    result["qualification"]["quarantined_count"] = len(result["quarantined"])
    if result["quarantined"]:
        result["qualification"]["status"] = "qualified_rows" if result["transactions"] else "quarantined_rows"
    result["materialized_extraction"] = extraction
    result["observation_materialization"] = {"source_sha256": source_sha,
        "observation_sha256": hashlib.sha256(json.dumps(observation, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "observed_data_row_count": len(extraction_rows),
        "unresolved_physical_row_count": len(unresolved_physical),
        "production_mutation_authorized": False}
    return result


def _asset_reading_is_bound(pdf_bytes: bytes, item: dict, cell: dict,
                            page: int, rendered: dict) -> bool:
    """Validate the existing second-reading crop without running OCR again."""
    import hashlib
    import io
    if (item.get("first_crop_sha256") != cell.get("crop_sha256") or
            item.get("first_ocr_raw") != cell.get("raw_text") or
            item.get("second_crop_render_dpi") != 300):
        return False
    box = cell.get("bbox_normalized")
    if (not isinstance(box, list) or len(box) != 4 or
            not all(type(value) in {float, int} for value in box) or
            not 0 <= box[0] < box[2] <= 1 or not 0 <= box[1] < box[3] <= 1):
        return False
    if page not in rendered:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            rendered[page] = pdf.pages[page - 1].to_image(resolution=300).original.convert("L")
    image = rendered[page]
    crop = image.crop((round(box[0] * image.width) + 3, round(box[1] * image.height) + 3,
                       round(box[2] * image.width) - 3, round(box[3] * image.height) - 3))
    encoded = io.BytesIO()
    crop.save(encoded, format="PNG")
    return hashlib.sha256(encoded.getvalue()).hexdigest() == item.get("second_crop_sha256")


def _bound_amount_column(pdf_bytes: bytes, reading: dict, physical: dict,
                          page: int, rendered: dict) -> dict | None:
    import hashlib
    import io
    from .house_ptr_amount_header import interpret_amount_column
    amount = interpret_amount_column(reading)
    if (amount is None or reading.get("render_dpi") != 300 or
            reading.get("image_rotation_degrees") != 270 or
            reading.get("coordinate_frame") != "original_page"):
        return None
    box = reading.get("bbox_pixels")
    if (not isinstance(box, list) or len(box) != 4 or
            not all(type(value) is int for value in box) or
            not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]):
        return None
    if page not in rendered:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            rendered[page] = pdf.pages[page - 1].to_image(resolution=300).original.convert("L")
    image = rendered[page]
    cells = [cell for cell in physical["cells"]["amount"] if cell.get("label") == reading["label"]]
    if len(cells) != 1 or box[2] > image.width or box[3] > image.height:
        return None
    bounds = cells[0]["bbox_normalized"]
    if (abs(box[0] / image.width - bounds[0]) > .006 or
            abs(box[2] / image.width - bounds[2]) > .006 or
            box[3] / image.height >= physical["band"][0]):
        return None
    crop = image.crop(tuple(box)).rotate(270, expand=True)
    encoded = io.BytesIO()
    crop.save(encoded, format="PNG")
    return amount if hashlib.sha256(encoded.getvalue()).hexdigest() == reading.get("crop_sha256") else None
