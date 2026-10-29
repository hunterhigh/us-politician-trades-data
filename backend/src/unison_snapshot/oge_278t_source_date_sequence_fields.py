"""Opt-in source-replayed comparison eligibility for an inserted OCR day digit."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path

MODE="source_pdf_date_sequences/v1"


def attach_source_date_sequences(result: dict, originals: list[dict], *, source_pdf_path) -> None:
    from .pdf_source_date_sequences import observe_source_date_sequences,compare_auxiliary_date
    from .pdf_text_baselines import source_text_baseline_bindings
    from .oge_278t_raster_slots import box_in_slot,slot_quad,column_geometry_sha256
    from .oge_278t_fields import _known_value,_comparable_value,_date_shape
    if source_pdf_path is None or not Path(source_pdf_path).is_file():
        raise ValueError("source date sequence mode requires controlled original PDF")
    source=Path(source_pdf_path);sha=result["source_sha256"]
    if hashlib.sha256(source.read_bytes()).hexdigest()!=sha:raise ValueError("source date sequence PDF SHA differs")
    source_pages={p["page_number"]:p for item in originals for p in item["pages"]};inputs=[];counts=Counter();rejections=[]
    for page in result["pages"]:
        if page["status"]!="field_shapes_observed":continue
        original=source_pages[page["page_number"]]
        if original.get("geometry_mode")!="source_pdf_dual_axis/v1":raise ValueError("source date sequence requires dual-axis Date geometry")
        observed=observe_source_date_sequences(source,expected_sha256=sha,page_number=page["page_number"])
        if observed.get("source_sha256")!=sha or observed.get("page_number")!=page["page_number"]:raise ValueError("source date sequence page differs")
        input_index=len(inputs);inputs.append(observed);candidates=[]
        for index,record in enumerate(observed["observations"]):
            if record.get("status")!="source_slash_nine_supported":continue
            matches=[]
            for slot in original["slots"]:
                indices=slot["column_observation"]["transaction_date"].get("word_indices",[])
                # No other Date object may be omitted from a whole-field comparison.
                if len(indices)!=1:continue
                word=slot["words"][indices[0]]
                if word["text"]==record["raw_text"] and word["bbox_pdf_points"]==record["bbox_displayed_mediabox"]:
                    matches.append((slot,indices[0],word))
            reason=None
            if len(matches)!=1:reason="source_Date_word_not_unique_complete_field"
            elif not box_in_slot(original,matches[0][0],matches[0][2]["bbox_pdf_points"],3,4):reason="source_Date_word_exceeds_field_quad"
            if reason:
                rejections.append({"page_number":page["page_number"],"observation_index":index,"reason":reason});continue
            source_slot,word_index,word=matches[0];target=next(s for s in page["slots"] if s["slot_index"]==source_slot["slot_index"])
            readings=target["field_readings"]["transaction_date"]["observations"]
            eligible=[]
            for read_index,read in enumerate(readings):
                if (read.get("method")!="column_ocr" or read.get("geometry_status")!="source_bound" or
                    read.get("read_mode")!="source_quad_cells/v1" or
                    read.get("source_quad_cell",{}).get("render_layer")!="source_text_objects_auxiliary"):continue
                explanation=compare_auxiliary_date(record,read["raw_ocr_text"])
                if explanation["status"]!="not_resolved":eligible.append((read_index,explanation))
            if eligible:candidates.append((index,record,source_slot,word_index,word,target,eligible))
        queries=[{"text":record["raw_text"],**dict(zip(("x0","top","x1","bottom"),record["bbox_displayed_mediabox"]))}
                 for _,record,_,_,_,_,_ in candidates]
        visibility=source_text_baseline_bindings(source,expected_sha256=sha,page_number=page["page_number"],page=original,words=queries) if queries else {"bindings":{}}
        for query_index,(index,record,slot,word_index,word,target,eligible) in enumerate(candidates):
            binding=visibility["bindings"].get(str(query_index))
            if (not binding or binding.get("source_object_index")!=record["source_object_index"] or
                binding.get("slot_index")!=slot["slot_index"] or binding.get("column_index")!=3 or
                binding.get("source_object_manifest_sha256")!=observed["source_text_manifest_sha256"] or
                binding.get("matrix_pdf_bottom_left")!=record["matrix_pdf_bottom_left"]):
                rejections.append({"page_number":page["page_number"],"observation_index":index,"reason":"source_date_sequence_visibility_unproved"});continue
            proof={"schema_version":"oge-source-date-sequence-comparison/v1","source_sha256":sha,
                "page_number":page["page_number"],"slot_index":slot["slot_index"],"source_object_index":record["source_object_index"],
                "source_ref":f"source_date_sequence_inputs/{input_index}/observations/{index}",
                "raw_text":record["raw_text"],"bbox_pdf_points":record["bbox_displayed_mediabox"],
                "field_quad_pdf_points":slot_quad(original,slot,3,4),"column_geometry_sha256":column_geometry_sha256(original),
                "visibility_evidence":binding}
            evidence=target["field_readings"]["transaction_date"];reads=evidence["observations"]
            for read_index,explanation in eligible:
                # The saved TSV, literal recognized shape and geometry remain intact.
                read=reads[read_index];read["comparison_eligible"]=False
                read["source_sequence_comparison"]={"explanation":explanation,"binding":deepcopy(proof)}
                counts["source_contradicted_auxiliary_reads"]+=1
            reads.append({"method":"source_glyph_sequence_interpretation","raw_ocr_text":record["raw_text"],
                "shape":{**_date_shape(record["raw_text"]),"status":"source_glyph_shape_only"},
                "geometry_status":"source_bound","geometry_issues":[],"source_ref":proof["source_ref"],
                "engine":"source_complete_CID_glyph_sequence/v1","confidence_basis":"not_applicable_not_ocr",
                "word_indices":[word_index],"source_sequence_binding":proof})
            values=[_comparable_value(r) for r in reads if r.get("geometry_status")!="ambiguous" and _comparable_value(r) is not None]
            evidence.update(consistency="conflicting_observations" if len({repr(v) for v in values})>1 else "matching_observations" if len(values)>1 else "single_recognized_observation",
                observation_count=len(reads),recognized_observation_count=sum(_known_value(r["shape"]) is not None for r in reads),
                comparable_observation_count=len(values),geometry_ambiguous_observation_count=sum(r.get("geometry_status")=="ambiguous" for r in reads))
            counts["source_Date_fields_with_sequence_proof"]+=1
    if "secondary_field_summary" in result:
        summary={k:v for k,v in result["secondary_field_summary"].items() if not k.startswith("transaction_date_")};dates=Counter()
        for page in result["pages"]:
            for slot in page.get("slots",[]):
                e=slot["field_readings"]["transaction_date"];dates['transaction_date_'+e['consistency']]+=1
                if e.get('geometry_ambiguous_observation_count'):dates['transaction_date_geometry_ambiguous_observations']+=e['geometry_ambiguous_observation_count']
        result["secondary_field_summary"]=dict(sorted({**summary,**dates}.items()))
    result.update(source_date_sequence_mode=MODE,source_date_sequence_inputs=inputs,
        source_date_sequence_summary=dict(sorted(counts.items())),source_date_sequence_rejections=rejections,
        field_evidence_schema_version="oge-278t-linked-field-evidence/v3-source-date-sequences")
