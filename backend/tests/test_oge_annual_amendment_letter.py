"""Local correction scope contracts; synthetic text is not source accuracy gold."""
from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image
from test_oge_annual_candidate import source, inputs, base, DOC, SHA, CONTENT
from unison_snapshot.oge import OgeCatalogError
from unison_snapshot.oge_annual_amendment_letter import interpret_amendment_letter, SCHEMA
from unison_snapshot.whitehouse_annual_candidate import overlay_oge_annual_candidate

PURPOSE = ("The purpose of this letter is to correct the amount of the advance I reported on Part 2, line 5 of "
           "my financial disclosure report that I signed on May 17, 2021. The correct amount is noted below. Correction to Part 2")

def observation(text=PURPOSE):
    rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for i,word in enumerate(text.split()):
        rows.append(f"5\t1\t1\t1\t1\t{i+1}\t10\t10\t10\t10\t99\t{word}")
    raw = "\n".join(rows)+"\n"
    image = Image.new("RGB", (1700,2200), (254,254,254))
    return {"schema_version":SCHEMA,"source_document_id":DOC,"source_sha256":SHA,
            "page_number":1,"dpi":200,"image_size":[1700,2200],"page_size":[612.,792.],
            "image_pixels_sha256":hashlib.sha256(image.tobytes()).hexdigest(),
            "raw_tsv":raw,"raw_tsv_sha256":hashlib.sha256(raw.encode()).hexdigest()},image

def relation(number="1"):
    return {"status":"local_target_value_unresolved","section":"part2","printed_row_number":number,
            "original_report_signed_date":"2026-06-01","holding_value_status":"unknown",
            "formal_corrections":[]}

class LocalAmendmentTests(unittest.TestCase):
    def test_raw_scope_recomputed_and_value_remains_unknown(self):
        o,image=observation();a=inputs(source())["archive_metadata_by_document"][DOC]
        a.update(amended_label="yes",document_url=a["document_url"].replace("example-annual","AMENDED-annual"))
        columns={"tracks":[[x,x] for x in (0,40,100,120,150,180,220)],
                 "body_words":[{"text":"5","box":[1,1,2,2]}, {"text":"advance","box":[151,1,170,2]}],"value_status":"unknown"}
        with patch("unison_snapshot.oge_annual_amendment_letter._source_image",return_value=(image,[],612.,792.)), patch("unison_snapshot.oge_annual_amendment_letter._columns",return_value=columns):
            result=interpret_amendment_letter(o,archive_metadata=a,archived_pdf_path="source.pdf")
            self.assertEqual(result["printed_row_number"],"5")
            self.assertEqual(result["holding_value_status"],"unknown")
            self.assertEqual(result["formal_corrections"],[])
            for text in [PURPOSE.replace("advance","value"),PURPOSE+" Also change Part 7 line 2."]:
                bad,_=observation(text)
                self.assertIsNone(interpret_amendment_letter(bad,archive_metadata=a,archived_pdf_path="source.pdf"))
            bad=deepcopy(o);bad["raw_tsv"]+="tamper"
            with self.assertRaises(OgeCatalogError):
                interpret_amendment_letter(bad,archive_metadata=a,archived_pdf_path="source.pdf")
            bad=deepcopy(o);bad["image_pixels_sha256"]="0"*64
            with self.assertRaises(OgeCatalogError):
                interpret_amendment_letter(bad,archive_metadata=a,archived_pdf_path="source.pdf")

    def _run(self,extracted,local):
        data=inputs(extracted);data["archive_metadata_by_document"][DOC]["amended_label"]="yes"
        if local is not None:
            data["amendment_letter_observations_by_document"]={DOC:{"observation":{},"archived_pdf_path":"source.pdf"}}
        with patch("unison_snapshot.oge_annual_amendment_letter.interpret_amendment_letter",return_value=local):
            return overlay_oge_annual_candidate(base(),data)

    def test_unknown_scope_still_blocks_every_section(self):
        result,audit=self._run(source(),None)
        self.assertEqual(result["reported_holdings"],[]);self.assertEqual(result["transactions"],[])
        self.assertIn("annual_amendment_relationship_unresolved",audit["reports"][0]["transaction_report_reasons"])

    def test_local_value_unknown_does_not_block_unrelated_part7(self):
        result,audit=self._run(source(),relation())
        self.assertEqual(result["reported_holdings"],[])
        self.assertEqual(len(result["transactions"]),1)
        r=audit["reports"][0]
        self.assertEqual(r["transaction_report_reasons"],[])
        self.assertIn("annual_amendment_target_value_unresolved",r["report_reasons"])
        self.assertEqual(r["local_amendment_relation"]["target_binding_status"],"unique_printed_row")

    def test_only_existing_official_unknown_partial_rule_allows_other_holdings(self):
        extracted=source(official_unknown=True)
        result,audit=self._run(extracted,relation("2"))
        normal,_=overlay_oge_annual_candidate(base(),inputs(extracted))
        self.assertEqual(result["reported_holdings"],normal["reported_holdings"])
        self.assertEqual(result["transactions"],normal["transactions"])
        self.assertEqual(len(result["reported_holdings"]),2)
        rows=audit["reports"][0]["row_qualification"]
        self.assertTrue(any("annual_amendment_target_value_unresolved" in r["reasons"] for r in rows))
        again,_=self._run(extracted,relation("2"))
        self.assertEqual(result["reported_holdings"],again["reported_holdings"])

    def test_missing_or_ambiguous_target_never_weakens_holding_selection(self):
        result,audit=self._run(source(),relation("99"))
        self.assertEqual(result["reported_holdings"],[])
        self.assertEqual(len(result["transactions"]),1)
        self.assertEqual(audit["reports"][0]["local_amendment_relation"]["target_binding_status"],"unresolved")

    def test_source_gray_columns_need_roles_and_repeated_support(self):
        from PIL import ImageDraw
        from unison_snapshot.oge_annual_amendment_letter import _columns
        image=Image.new("RGB",(1700,2200),(254,254,254));draw=ImageDraw.Draw(image)
        xs=[70,100,300,335,395,480,550]
        for x in xs:draw.line((round(x/.36),round(337/.36),round(x/.36),round(422/.36)),fill=(200,200,200),width=2)
        words=[{"text":t,"box":b} for t,b in [
            ("#",[80,341,86,350]),("Description",[105,341,165,350]),("EIF",[307,341,327,350]),
            ("Value",[340,341,369,350]),("Income",[402,341,439,350]),("Type",[442,341,469,350]),
            ("Income",[484,341,520,350]),("Amount",[484,355,526,364]),("5",[80,373,86,381]),
            ("advance",[402,373,441,381])]]
        lines=[{"x0":70,"x1":550,"top":422,"bottom":422}]
        self.assertIsNotNone(_columns(image,words,lines))
        missing=image.copy();ImageDraw.Draw(missing).rectangle((round(334/.36),round(337/.36),round(336/.36),round(422/.36)),fill=(254,254,254))
        self.assertIsNone(_columns(missing,words,lines))
        wrong=deepcopy(words);wrong[3]["box"]=[302,341,329,350]
        self.assertIsNone(_columns(image,wrong,lines))
        extra=image.copy();ImageDraw.Draw(extra).line((round(355/.36),round(337/.36),round(355/.36),round(422/.36)),fill=(200,200,200),width=2)
        self.assertIsNone(_columns(extra,words,lines))

    def test_original_report_date_conflict_is_not_a_new_filing_date(self):
        bad=relation();bad["original_report_signed_date"]="2026-06-02"
        result,audit=self._run(source(),bad)
        self.assertEqual(result["transactions"],[])
        self.assertIn("annual_amendment_original_report_date_conflict",audit["reports"][0]["report_reasons"])

    def test_pdf_byte_mismatch_rejected_before_render_or_ocr(self):
        from tempfile import TemporaryDirectory
        from unison_snapshot.oge_annual_amendment_letter import _source_image
        with TemporaryDirectory() as folder:
            pdf=Path(folder)/"wrong.pdf";pdf.write_bytes(CONTENT+b"changed")
            with self.assertRaisesRegex(OgeCatalogError,"differs from source"):
                _source_image(pdf,SHA)
