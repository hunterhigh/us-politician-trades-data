"""Synthetic template/negative checks; the real fixed PDF is separately replayed."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from unison_snapshot.house_ptr import parse_word_pages, qualify_automatic
from unison_snapshot.house_native_coverage import GEOMETRY_SCHEMA
from unison_snapshot.house import HouseIndexError
from test_house_ptr import fixture_pages, word, META, IDENTITY

SHA = "a" * 64


def geometry(number):
    return {"schema_version": GEOMETRY_SCHEMA, "source_sha256": SHA,
        "page_number": number,"width": 612,"height": 792,
        "rects": [],"curves": [],"images": []}


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
