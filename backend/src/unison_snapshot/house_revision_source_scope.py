"""Compose original-bound House native ID evidence before destructive revisions.

This profile deliberately requires the whole fixed native source. Saved OCR,
cached transaction IDs and a source-context receipt never certify absence of a
competing predecessor. Results are diagnostics; consumers must call this reader
against their selected originals rather than accepting a saved positive token.
"""
from __future__ import annotations

from contextlib import ExitStack
from hashlib import sha256
import io


VERSION = "house-native-revision-source-scope/v1"
MAX_PAGES = 32
MAX_CHARACTERS = 25_000
MAX_WORD_COMPARISONS = 200_000


class _Unknown(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise _Unknown(reason)


def _charge(budget, count):
    if budget is not None:
        budget["comparisons"] += count
        _need(budget["comparisons"] <= MAX_WORD_COMPARISONS, "source_scope_comparison_budget_exceeded")


def _inside(outer, inner):
    return (inner is not None and outer[0] <= inner[0] < inner[2] <= outer[2]
            and outer[1] <= inner[1] < inner[3] <= outer[3])


def _overlaps(a, b):
    return (a is not None and b is not None and max(a[0], b[0]) < min(a[2], b[2])
            and max(a[1], b[1]) < min(a[3], b[3]))


def _validate_source_character_bindings(occurrences, binding_result, fonts):
    """Bind independently parsed source CIDs, font programs and Unicode maps."""
    _need(binding_result.get("status") == "observed", "source_character_binding_unknown")
    chars, bindings = binding_result["chars"], binding_result["bindings"]
    _need(len(occurrences) == len(chars) == len(bindings),
          "source_character_and_paint_inventory_differ")
    for index, (occurrence, binding, char) in enumerate(zip(occurrences, bindings, chars)):
        pointer, semantics = fonts[occurrence["font_ref"]]
        _need(binding["char_index"] == binding["render_ordinal"] == occurrence["occurrence"] == index
              and binding["cid"] == occurrence["cid"]
              and binding["source_object_id"] == pointer.source_object_id
              and binding["source_generation"] == pointer.source_generation,
              "source_character_CID_or_font_membership_differs")
        _need(binding.get("font_file_sha256") == semantics["source_font_sha256"]
              == occurrence["font_program_sha256"] == sha256(pointer.program_bytes).hexdigest(),
              "source_character_font_program_differs")
        _need(binding.get("ToUnicode_sha256") == semantics["source_ToUnicode_sha256"]
              == sha256(pointer.to_unicode_bytes).hexdigest(), "source_character_ToUnicode_program_differs")
        glyph = semantics["glyphs"][occurrence["cid"]]
        _need(glyph["gid"] == occurrence["gid"], "source_character_selected_glyph_differs")
        _need(glyph["source_unicode"] == char["text"] == binding["text"],
              "source_character_Unicode_differs")


def _group_domain(occurrences, indices, inventory, *, allow_header_background=False,
                  allow_status_color=False, budget=None):
    """Exclude only the independently bound declaration's own source events."""
    _charge(budget, len(indices) + len(inventory))
    selected = [occurrences[i] for i in indices]
    _need(bool(selected) and len(set(indices)) == len(indices), "declaration_glyph_membership_invalid")
    conditional = ({"nonglyph_paint_domain_intersects"} if allow_header_background else set())
    if allow_status_color:
        conditional.add("target_fill_not_black")
    _need(all(v["outer_device_pixels"] is not None
              and not (set(v["unresolved"]) - conditional) for v in selected),
          "declaration_glyph_paint_unknown")
    color = [0, 0, 0]
    if allow_status_color:
        colors = [v.get("display_bytes") for v in selected]
        _need(all(v.get("opaque") is True and v.get("engine_color_source_verified") is True for v in selected)
              and all(isinstance(v, list) and len(v) == 3
                  and all(type(c) is int and 0 <= c <= 255 for c in v) for v in colors)
              and all(v == colors[0] for v in colors), "status_display_color_unproven")
        color = colors[0]
        _need(color != [255, 255, 255], "status_text_has_no_white_paper_contrast")
    box = [min(v["outer_device_pixels"][0] for v in selected),
           min(v["outer_device_pixels"][1] for v in selected),
           max(v["outer_device_pixels"][2] for v in selected),
           max(v["outer_device_pixels"][3] for v in selected)]
    own_events = {v["paint_event"] for v in selected}
    _need(len(own_events) == len(selected), "declaration_paint_membership_duplicated")
    for event in inventory:
        if event["event"] in own_events or not _overlaps(box, event["outer_device_pixels"]):
            continue
        background = event.get("uniform_background")
        _need(allow_header_background and event["event"] < min(own_events)
              and isinstance(background, dict) and background.get("status") == "observed"
              and background.get("opaque") is True
              and _inside(background.get("inner_device_pixels", []), box)
              and background.get("display_bytes") not in (None, color),
              "declaration_intersects_other_source_paint")
    return box


def inspect_native_house_revision_source(source):
    """Reopen an immutable context document and prove its native ID inventory.

    The context loader authenticates official identity/index selection. This
    function adds complete native role/projection, source font semantics,
    original character/CID membership and declaration paint. It never resolves
    a revision chain or grants an ordinary financial row qualification.
    """
    result = {"schema_version": VERSION, "document_id": source.document_id,
              "source_sha256": source.source_sha256, "status": "unknown",
              "scope_complete": False, "pages": [], "reasons": []}
    budget = {"comparisons": 0, "limit": MAX_WORD_COMPARISONS}
    result["budget"] = budget
    try:
        import fontTools
        import pdfplumber
        from fontTools.ttLib import TTFont
        from .house_id_source_fonts import observe_house_id_font, _directory, MAX_FONT_BYTES
        from .house_id_source_paint import GlyphOutline, inspect_house_id_source_paint
        from .house_id_source_characters import observe_house_id_source_characters
        from .house_native_id_declarations import observe_native_house_id_declarations
        from .house_native_coverage import _text
        from .house_ptr import observe_native_word_pages, parse_word_pages, planned_ocr_pages
        from pdfplumber.utils.text import WordExtractor

        _need(fontTools.__version__ == "4.62.1", "source_font_parser_version_unsupported")
        _need(sha256(source.pdf_bytes).hexdigest() == source.source_sha256, "source_original_hash_mismatch")
        metadata, saved = source.metadata, source.extraction
        _need(metadata.get("sha256") == source.source_sha256 and saved is not None,
              "source_metadata_or_selected_extraction_unavailable")
        _need(not source.planned_ocr_pages, "opaque_source_cannot_prove_competitor_absence")
        with ExitStack() as stack:
            document = stack.enter_context(pdfplumber.open(io.BytesIO(source.pdf_bytes)))
            _need(1 <= len(document.pages) <= MAX_PAGES, "source_page_budget_exceeded")
            bound = [observe_house_id_source_characters(page) for page in document.pages]
            _need(all(v["status"] == "observed" for v in bound), "source_character_binding_unknown")
            _need(sum(len(v["chars"]) for v in bound) <= MAX_CHARACTERS, "source_character_budget_exceeded")
            pages = observe_native_word_pages(document, source.source_sha256)
            _need(not planned_ocr_pages(pages), "opaque_source_cannot_prove_competitor_absence")
            replay = parse_word_pages(metadata, source.source_sha256, pages,
                                      copy_allowed=getattr(document.doc, "is_extractable", None))
            _need(replay == saved, "selected_extraction_differs_from_original_native_projection")
            ledger = replay.get("source_report_ledger", {})
            _need(ledger.get("relation_coverage") == "accounted"
                  and len(ledger.get("pages", [])) == len(pages)
                  and all(p.get("coverage") in {"located", "non_table"} for p in ledger["pages"]),
                  "whole_source_native_roles_unaccounted")
            raw_fonts, outlines = {}, {}

            def outline(data, gid):
                digest = sha256(data).hexdigest()
                _need(len(data) <= MAX_FONT_BYTES, "source_font_byte_budget_exceeded")
                if digest not in raw_fonts:
                    _directory(data)
                    font = stack.enter_context(TTFont(io.BytesIO(data), lazy=True))
                    _need(0 < font["maxp"].numGlyphs <= 8192, "source_font_glyph_budget_exceeded")
                    font.setGlyphOrder([f"gid{i:05}" for i in range(font["maxp"].numGlyphs)])
                    raw_fonts[digest] = font
                key = (digest, gid)
                if key not in outlines:
                    font = raw_fonts[digest]
                    _need(type(gid) is int and 0 <= gid < font["maxp"].numGlyphs,
                          "source_glyph_index_unavailable")
                    glyph = font["glyf"][font.getGlyphOrder()[gid]]
                    _need(not glyph.isComposite(), "source_composite_glyph_unsupported")
                    points, _, _ = glyph.getCoordinates(font["glyf"])
                    _need(len(points) <= 4096, "source_glyph_point_budget_exceeded")
                    outlines[key] = GlyphOutline(font["head"].unitsPerEm,
                                                tuple((int(x), int(y)) for x, y in points))
                return outlines[key]

            for number, (page, chars) in enumerate(zip(pages, bound), 1):
                fonts = {}

                def observe_font(pointer):
                    _need(pointer.source_generation == 0, "source_font_generation_unsupported")
                    spec = document.doc.getobj(pointer.source_object_id)
                    observed = observe_house_id_font(spec, pointer.program_bytes, pointer.used_cids)
                    _need(observed["status"] == "observed", "source_used_font_semantics_unknown:" + str(observed["reason"]))
                    _need(observed["source_ToUnicode_sha256"] == sha256(pointer.to_unicode_bytes).hexdigest(),
                          "source_font_reader_ToUnicode_mismatch")
                    for gid, glyph in observed["glyphs"].items():
                        raw = outline(pointer.program_bytes, glyph["gid"])
                        _need(raw.units_per_em == glyph["units_per_em"] and list(map(list, raw.points)) == glyph["points"],
                              "source_outline_reader_mismatch")
                    fonts[pointer.font_ref] = (pointer, observed)

                paint = inspect_house_id_source_paint(source.pdf_bytes, source_sha256=source.source_sha256,
                    page_number=number, font_program_loader=outline, font_source_observer=observe_font)
                _need(paint["status"] == "observed" and paint["processed_pages"] == [number]
                      and paint["page_count"] == len(pages), "source_paint_inventory_unknown")
                occurrences = paint["occurrences"]
                _charge(budget, len(occurrences))
                _validate_source_character_bindings(occurrences, chars, fonts)
                tuples = list(WordExtractor(x_tolerance=2, y_tolerance=2,
                              extra_attrs=["size"]).iter_extract_tuples(chars["chars"]))
                _need([word for word, _ in tuples] == page["words"], "source_word_membership_projection_differs")
                ordinals = {id(char): i for i, char in enumerate(chars["chars"])}
                members = [[ordinals[id(char)] for char in word_chars] for _, word_chars in tuples]
                declaration = observe_native_house_id_declarations(page, chars["chars"],
                    source_sha256=source.source_sha256, page_number=number, source_rows=replay["transactions"])
                _need(declaration["status"] in {"observed_declarations", "observed_role"},
                      "source_ID_declaration_roles_unknown")
                # An invisible header cannot assign an ID-column role. These
                # are the exact eight-column labels actually used by the
                # established native grammar, not all incidental page words.
                anchors = []
                cells = declaration["role_evidence"].get("header_cells", [])
                header_indices = declaration["role_evidence"].get("header_word_indices", [])
                if cells:
                    expected_labels = ["ID", "Owner", "Asset", "Transaction Type", "Date",
                                       "Notification Date", "Amount", "Cap. Gains > $200?"]
                    _charge(budget, len(cells) * len(page["words"]))
                    exact_members = [[i for i, word in enumerate(page["words"])
                        if _inside(cell, [word[k] for k in ("x0", "top", "x1", "bottom")])] for cell in cells]
                    _need([_text([page["words"][i] for i in group]) for group in exact_members] == expected_labels
                          and sorted(i for group in exact_members for i in group) == sorted(header_indices),
                          "source_header_anchors_not_exactly_accounted")
                for index in header_indices:
                    domain = _group_domain(occurrences, members[index], paint["paint_inventory"],
                                           allow_header_background=True, budget=budget)
                    _need(sum(_inside(cell, domain) for cell in declaration["role_evidence"]["header_cells"]) == 1,
                          "header_actual_paint_outside_unique_source_cell")
                    anchors.append({"word_index": index, "source_paint_domain": domain})
                declarations = []
                for item in declaration["declarations"]:
                    indices = item["source_character_indices"]
                    digits = []
                    for index in indices:
                        occurrence = occurrences[index]
                        glyph = fonts[occurrence["font_ref"]][1]["glyphs"][occurrence["cid"]]
                        _need(glyph["numeric"] is True and isinstance(glyph["source_unicode"], str)
                              and glyph["source_unicode"] in "0123456789", "official_ID_glyph_not_proven_digit")
                        digits.append(glyph["source_unicode"])
                    _need("".join(digits) == item["reported_transaction_id"], "official_ID_source_text_differs")
                    domain = _group_domain(occurrences, indices, paint["paint_inventory"], budget=budget)
                    header = declaration["role_evidence"]["header_cells"][0]
                    _need(header[0] <= domain[0] < domain[2] <= header[2] and domain[1] >= header[3],
                          "official_ID_actual_paint_outside_declared_column")
                    rows = [replay["transactions"][i] for i in item["source_row_indices"]]
                    boxes = [s["bbox_points"] for row in rows for s in row["evidence"]["segments"]
                             if s["page"] == number]
                    _need(any(_inside(box, domain) for box in boxes), "official_ID_actual_paint_outside_source_row")
                    declarations.append({**item, "source_paint_domain": domain})
                if declarations:
                    # Numerical text shifted into the real ID strip cannot
                    # disappear merely because a different text-layout engine
                    # assigned it to a date/amount/asset word.
                    header = declaration["role_evidence"]["header_cells"][0]
                    represented = {i for item in declarations for i in item["source_character_indices"]}
                    table_bottom = max(s["bbox_points"][3] for row in replay["transactions"]
                        for s in row["evidence"]["segments"] if s["page"] == number)
                    strip = [header[0], header[3], header[2], table_bottom]
                    for index, occurrence in enumerate(occurrences):
                        _charge(budget, 1)
                        glyph = fonts[occurrence["font_ref"]][1]["glyphs"][occurrence["cid"]]
                        if glyph["numeric"] and _overlaps(strip, occurrence["outer_device_pixels"]):
                            _need(index in represented, "unrepresented_numeric_paint_in_ID_column")
                    for row in replay["transactions"]:
                        boxes = [s["bbox_points"] for s in row["evidence"]["segments"] if s["page"] == number]
                        _charge(budget, len(page["words"]) * max(1, len(boxes)))
                        status_words = [i for i, word in enumerate(page["words"])
                            if word["text"] == row.get("filing_status")
                            and any(_inside(box, [word[k] for k in ("x0", "top", "x1", "bottom")]) for box in boxes)]
                        # Continuation pages may carry no repeated status; the
                        # full native ledger checks its uniqueness across pages.
                        for index in status_words:
                            domain = _group_domain(occurrences, members[index], paint["paint_inventory"],
                                                   allow_status_color=True, budget=budget)
                            _need(any(_inside(box, domain) for box in boxes), "filing_status_actual_paint_outside_source_row")
                            anchors.append({"word_index": index, "source_paint_domain": domain})
                result["pages"].append({"page_number": number, "status": "observed",
                    "declarations": declarations, "character_count": len(occurrences),
                    "anchors": anchors,
                    "paint_event_count": len(paint["paint_inventory"]),
                    "font_profiles": sorted({v[1]["reference_profile"] for v in fonts.values()})})
            result.update(status="observed_scope", scope_complete=True)
    except _Unknown as error:
        result["reasons"] = [str(error)]
    except ImportError:
        result["reasons"] = ["source_scope_dependencies_unavailable"]
    except Exception as error:
        result["reasons"] = ["source_scope_observation_failed:" + type(error).__name__]
    return result
