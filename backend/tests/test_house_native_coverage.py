"""Synthetic template/negative checks; the real fixed PDF is separately replayed."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house_ptr import parse_word_pages, qualify_automatic
from unison_snapshot.house_native_coverage import GEOMETRY_SCHEMA, observe_native_geometry
from unison_snapshot.house import HouseIndexError
from test_house_ptr import fixture_pages, word, META, IDENTITY

SHA = "a" * 64


def geometry(number):
    return {"schema_version": GEOMETRY_SCHEMA, "source_sha256": SHA,
        "page_number": number,"width": 612,"height": 792,
        "lines": [],"rects": [],"curves": [],"images": []}


def template():
    page=fixture_pages()[0]
    page["words"]=[w for w in page["words"] if w["top"]<393]
    for w in page["words"]:
        if w["top"] == 40 and w["x0"] >= 480:
            w["top"] = 73
    page["words"] += [word("F I",22,150,12),word("Name: Example Person",22,190),
        word("Status: Member",22,210),word("State/District: NJ07",22,230),
        word("T",22,256,12)]
    edges=[22,62,101,258,323,379,443,522,576]
    names=["ID","Owner","Asset","Transaction Type","Date","Notification Date","Amount","Cap. Gains > $200?"]
    for x,name in zip(edges,names):page["words"].append(word(name,x+3,282))
    g=geometry(1)
    g["rects"]=[[a,274,b,318] for a,b in zip(edges,edges[1:])]
    g["rects"] += [[x,t,x+.75,z] for x in [22,576] for t,z in [(318,352),(352,386)]]
    g["images"]=[[55,72,109,122]]
    page["native_page_geometry"]=g
    second={"width":612,"height":792,"native_page_geometry":geometry(2),"words":[
        word("* For the complete list of asset type abbreviations, please visit https://fd.house.gov/reference/asset-type-codes.aspx.",25,76,8.25),
        word("Investment Vehicle Details",22,104,12),word("Account One",44,131),
        word("Comments",22,200,12),word("Third party advisors initiate these transactions.",22,220),
        word("Initial Public Offering",22,249,12),word("Yes No",40,272),
        word("Certification and Signature",22,302,12),
        word("I CERTIFY that the statements I have made on the attached Periodic Transaction Report are true, complete, and correct to the best of",39,325),
        word("my knowledge and belief. Further, I CERTIFY that I have disclosed all transactions as required by the STOCK Act.",22,335),
        word("Digitally Signed: Example Person, 09/12/2026",22,354)]}
    second["native_page_geometry"]["rects"]=[[22,123,576,181],[22.375,123.375,575.625,180.625]]
    return [page,second]


def mixed_template():
    source=template();first,closing=source
    g=first["native_page_geometry"]
    for b in g["rects"]:
        if b[1]==352:b[3]=410
    first["words"] += [word("Description: Explicit official note with $50,000 mentioned.",105,370,8.5),
                       word("Second official description line.",105,385,8.5)]
    for w in closing["words"]:
        w=deepcopy(w);w["top"]+=344;first["words"].append(w)
    for kind in ("lines","rects","curves","images"):
        for b in closing["native_page_geometry"][kind]:
            first["native_page_geometry"][kind].append([b[0],b[1]+344,b[2],b[3]+344])
    return [first]


def crosspage_template():
    first,closing=template();original=deepcopy(first)
    first["words"]=[w for w in first["words"] if w["top"]!=354 and not (w["top"]==326 and w["text"]=="[ST]")]
    g=first["native_page_geometry"]
    g["rects"]=[b for b in g["rects"] if b[1]!=352]
    second={"width":612,"height":792,"words":[],"native_page_geometry":geometry(2)}
    for w in original["words"]:
        if w["top"]==282:
            w=deepcopy(w);w["top"]=79;second["words"].append(w)
    for w in original["words"]:
        if 318<=w["top"]<386:
            w=deepcopy(w);w["top"]-=153;second["words"].append(w)
    second["words"] += [word("[ST]",105,116.7),word("Filing Status: New",105,134.3,8.5)]
    edges=[22,62,101,258,323,379,443,522,576]
    sg=second["native_page_geometry"]
    sg["rects"]=[[a,72,b,115.5] for a,b in zip(edges,edges[1:])]
    spans=[(99,132),(115.5,149.25),(132,165.75),(165.75,198.75),(198.75,233.25)]
    sg["rects"] += [[x,t,x+.75,z] for x in (22,576) for t,z in spans]
    sg["curves"]=[[544.6,107.4,554.1,116.85],[544.6,174.15,554.1,183.6]]
    for w in closing["words"]:
        w=deepcopy(w);w["top"]+=162;second["words"].append(w)
    for kind in ("lines","rects","curves","images"):
        for b in closing["native_page_geometry"][kind]:sg[kind].append([b[0],b[1]+162,b[2],b[3]+162])
    return [first,second]


class NativeCoverageTests(unittest.TestCase):
    def parse(self,pages):
        return parse_word_pages(META,SHA,pages,copy_allowed=True)

    def test_source_bands_status_words_and_closing_sections_accounted(self):
        source=template();without=deepcopy(source)
        for page in without:page.pop("native_page_geometry")
        x=self.parse(source);old=self.parse(without)
        self.assertEqual(x["transactions"],old["transactions"])
        self.assertEqual([p["coverage"] for p in x["source_report_ledger"]["pages"]],["located","non_table"])
        self.assertEqual(x["source_report_ledger"]["relation_coverage"],"accounted")
        self.assertEqual(x["source_report_ledger"]["disposition_counts"],{"data_candidate":2,"heading":1})
        self.assertEqual(x["source_report_ledger"]["logical_row_count"],1)
        self.assertEqual(qualify_automatic(x,IDENTITY)["transactions"],qualify_automatic(old,IDENTITY)["transactions"])
        self.assertEqual(self.parse(source),x)

    def test_absent_geometry_broken_bands_unmatched_status_or_extra_table_remain_unknown(self):
        for mutation in ("no_geometry","bad_source","bad_page","broken","no_status","extra_status","extra_header","extra_financial","extra_vector_table","interior_table"):
            with self.subTest(mutation=mutation):
                pages=template();g=pages[0]["native_page_geometry"]
                if mutation=="no_geometry":pages[0].pop("native_page_geometry")
                if mutation=="bad_source":g["source_sha256"]="b"*64
                if mutation=="bad_page":g["page_number"]=2
                if mutation=="broken":g["rects"].pop()
                if mutation=="no_status":pages[0]["words"]=[w for w in pages[0]["words"] if w["top"]!=354]
                if mutation=="extra_status":pages[0]["words"].append(word("Filing Status: New",105,380,8.5))
                if mutation=="interior_table":g["rects"].append([120,330,200,345])
                if mutation=="extra_vector_table":g["rects"].append([22,450,576,500])
                if mutation=="extra_header":pages[0]["words"].append(word("Amount",450,410))
                if mutation=="extra_financial":pages[0]["words"].extend([word("P",262,341),word("$1,000",446,341)])
                self.assertEqual(self.parse(pages)["source_report_ledger"]["pages"][0]["coverage"],"unknown")

    def test_closing_page_financial_extra_content_unknown_page_or_image_not_called_empty(self):
        for mutation in ("comment_financial","extra_financial","unknown_words","image","curve","table","interior_table","unknown_page"):
            with self.subTest(mutation=mutation):
                pages=template();p=pages[1];g=p["native_page_geometry"]
                if mutation=="comment_financial":p["words"].append(word("$1,001",446,235))
                if mutation=="extra_financial":p["words"].extend([word("New Corp [ST]",105,410),word("P",262,410),word("$1,001",446,410)])
                if mutation=="unknown_words":p["words"].append(word("Unclassified attachment",25,410))
                if mutation=="image":g["images"].append([100,210,300,230])
                if mutation=="curve":g["curves"].append([100,410,110,420])
                if mutation=="interior_table":g["rects"].append([50,135,250,160])
                if mutation=="table":g["rects"].append([20,410,500,500])
                if mutation=="unknown_page":pages.append({"width":612,"height":792,"words":[word("Unidentified attachment",25,100)]})
                x=self.parse(pages)
                self.assertEqual(x["source_report_ledger"]["relation_coverage"],"partial_unknown")
                self.assertIn("unknown",[p["coverage"] for p in x["source_report_ledger"]["pages"]])

    def test_official_closing_omits_empty_comments_but_no_other_sections(self):
        for comments in ("absent","empty"):
            with self.subTest(comments=comments):
                source=template()
                source[1]["words"]=[w for w in source[1]["words"] if w["top"]!=220
                    and not (comments=="absent" and w["top"]==200)]
                x=self.parse(source)
                self.assertEqual(x["source_report_ledger"]["relation_coverage"],"accounted")
                for mutation in ("missing_ipo","unknown_heading","financial_body","extra_lines","extra_image"):
                    with self.subTest(mutation=mutation):
                        bad=deepcopy(source);p=bad[1]
                        if mutation=="missing_ipo":p["words"]=[w for w in p["words"] if w["top"]!=249]
                        if mutation=="unknown_heading":p["words"].append(word("Unclassified Section",22,200,12))
                        if mutation=="financial_body":p["words"].append(word("$1,001",446,220))
                        if mutation=="extra_lines":p["native_page_geometry"]["lines"].append([20,210,500,210])
                        if mutation=="extra_image":p["native_page_geometry"]["images"].append([100,210,300,230])
                        self.assertEqual(self.parse(bad)["source_report_ledger"]["pages"][1]["coverage"],"unknown")

    def test_same_page_table_description_and_closing_are_composed(self):
        source=mixed_template();x=self.parse(source)
        self.assertEqual(x["source_report_ledger"]["relation_coverage"],"accounted")
        self.assertEqual(x["source_report_ledger"]["pages"][0]["coverage"],"located")
        self.assertIn("Second official description line.",x["transactions"][0]["description"])
        self.assertEqual(x["source_report_ledger"]["disposition_counts"],{"data_candidate":2,"heading":1,"note":1})
        bad=deepcopy(source)
        bad[0]["words"] += [word("Another [ST]",105,397,8.5),word("P",262,397,8.5),
            word("08/12/2026",327,397,8.5),word("$1,001",446,397,8.5)]
        self.assertEqual(self.parse(bad)["source_report_ledger"]["relation_coverage"],"partial_unknown")
        bad=deepcopy(source);bad[0]["words"].append(word("Unassigned gap",105,415))
        self.assertEqual(self.parse(bad)["source_report_ledger"]["relation_coverage"],"partial_unknown")

    def test_crosspage_repeated_header_collects_all_source_refs(self):
        source=crosspage_template();x=self.parse(source)
        self.assertEqual(len(x["transactions"]),2)
        self.assertEqual(x["source_report_ledger"]["relation_coverage"],"accounted")
        self.assertEqual([p["coverage"] for p in x["source_report_ledger"]["pages"]],["located","located"])
        refs=x["source_report_ledger"]["logical_rows"][0]["slot_refs"]
        self.assertEqual({r["page_number"] for r in refs},{1,2})
        self.assertEqual(len(refs),2)
        self.assertEqual(self.parse(source),x)
        for mutation in ("no_continuation","no_header","no_status","extra_control","unbound_curve"):
            with self.subTest(mutation=mutation):
                bad=deepcopy(source);page=bad[1]
                if mutation=="no_continuation":page["words"]=[w for w in page["words"] if w["top"]!=116.7]
                if mutation=="no_header":page["words"]=[w for w in page["words"] if w["top"]!=79]
                if mutation=="no_status":page["words"]=[w for w in page["words"] if w["top"]!=134.3]
                if mutation=="extra_control":page["native_page_geometry"]["curves"].append([544.6,150,554.1,159.45])
                if mutation=="unbound_curve":page["native_page_geometry"]["curves"].append([300,120,310,130])
                self.assertEqual(self.parse(bad)["source_report_ledger"]["relation_coverage"],"partial_unknown")

    def test_region_primitive_identity_must_have_exactly_one_role(self):
        from unison_snapshot.house_native_coverage import _split_closing_region
        page=mixed_template()[0];g=page["native_page_geometry"]
        self.assertIsNotNone(_split_closing_region(page,g))
        # Old aggregate counts could cancel: first primitive belongs to both
        # groups, second to neither. Per-primitive conservation must reject.
        g["curves"] += [[100,419.8,110,420.2],[100,410,110,430]]
        self.assertIsNone(_split_closing_region(page,g))

    def test_padding_cannot_assign_one_word_to_adjacent_bands_twice(self):
        from unison_snapshot.house_native_coverage import _in
        source=template()
        crossing={**word("Continuation",110,351.6),"x1":160,"bottom":352.4}
        self.assertTrue(_in(crossing,[22,318,576,352]))
        self.assertTrue(_in(crossing,[22,352,576,386]))
        source[0]["words"].append(crossing)
        x=self.parse(source)
        self.assertEqual(x["source_report_ledger"]["pages"][0]["coverage"],"unknown")
        self.assertEqual(x["source_report_ledger"]["relation_coverage"],"partial_unknown")

    def test_line_producer_and_unclassified_line_frames_fail_closed(self):
        class FakePage:
            width=612; height=792
            rects=[];curves=[];images=[]
            lines=[{"x0":20,"top":410,"x1":500,"bottom":410},
                   {"x0":500,"top":410,"x1":500,"bottom":500},
                   {"x0":20,"top":500,"x1":500,"bottom":500},
                   {"x0":20,"top":410,"x1":20,"bottom":500}]
        observed=observe_native_geometry(FakePage(),SHA,2)
        self.assertEqual(observed["lines"],[[20.,410.,500.,410.],
            [500.,410.,500.,500.],[20.,500.,500.,500.],[20.,410.,20.,500.]])
        for page_number in (1,2):
            for mutation in ("line_frame","missing_lines","bad_line"):
                with self.subTest(page=page_number,mutation=mutation):
                    source=template();g=source[page_number-1]["native_page_geometry"]
                    if mutation=="line_frame":g["lines"]=deepcopy(observed["lines"])
                    if mutation=="missing_lines":g.pop("lines")
                    if mutation=="bad_line":g["lines"]=[[20,410,500,float("nan")]]
                    if mutation=="bad_line":
                        # Saved words reject nonfinite cache values before any
                        # complete-page assertion can escape the parser.
                        with self.assertRaises(ValueError):self.parse(source)
                        continue
                    x=self.parse(source)
                    self.assertEqual(x["source_report_ledger"]["pages"][page_number-1]["coverage"],"unknown")
                    self.assertEqual(x["source_report_ledger"]["relation_coverage"],"partial_unknown")
                    self.assertEqual(len(qualify_automatic(x,IDENTITY)["transactions"]),1)

    def test_geometry_is_part_of_cache_replay_and_old_word_cache_unchanged(self):
        x=self.parse(template())
        forged=deepcopy(x);forged["source_word_pages"][0]["native_page_geometry"]["rects"].pop()
        with self.assertRaises(HouseIndexError):qualify_automatic(forged,IDENTITY)
        forged=deepcopy(x);forged["source_report_ledger"]["pages"][0]["coverage"]="unknown"
        with self.assertRaises(HouseIndexError):qualify_automatic(forged,IDENTITY)
        from unison_snapshot.house_ptr import _modern_report_ledger
        forged=deepcopy(x)
        forged["source_report_ledger"]=_modern_report_ledger(SHA,forged["source_word_pages"],
            forged["transactions"],include_unanchored=False)
        with self.assertRaises(HouseIndexError):qualify_automatic(forged,IDENTITY)
        old=template()
        for page in old:page.pop("native_page_geometry")
        oldx=self.parse(old)
        self.assertEqual(oldx["source_report_ledger"]["relation_coverage"],"partial_unknown")
        self.assertEqual(qualify_automatic(oldx,IDENTITY)["transactions"],qualify_automatic(x,IDENTITY)["transactions"])


if __name__=="__main__":unittest.main()
