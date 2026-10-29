"""Synthetic date pixel/consumer contracts, separate from fixed-source accuracy."""
from copy import deepcopy
import hashlib
import unittest
from unittest.mock import patch
try:
    from PIL import Image,ImageDraw,ImageFont
except ImportError:
    Image=ImageDraw=ImageFont=None
from unison_snapshot.senate import SenateEfdError
from unison_snapshot.senate_paper_dates import segment_geometry,saved_segmented_date,interpret_segmented_date
from unison_snapshot.senate_paper import parse_paper_grid_observations
from tests.test_senate_paper_grid import inputs


def fixture(text="7/9/26"):
    image=Image.new("RGB",(260,90),"white")
    ImageDraw.Draw(image).text((12,20),text,font=ImageFont.load_default(size=34),fill="black")
    geometry=segment_geometry(image)
    reads=[{"role":role,"box_px":box,"text":raw,"confidence":.99}
        for role,box,raw in zip(["month","day","year"],geometry.get("numeric_component_boxes_px",[]),["7","9","26"])]
    record=saved_segmented_date(image,page_sha256="a"*64,page_number=2,grid_slot=1,date_box_px=[0,0,260,90],reads=reads,
                               engine={"name":"synthetic_reader_contract"})
    return image,record


def read(image,record):
    return interpret_segmented_date(image,record,page_sha256="a"*64,page_number=2,grid_slot=1,date_box_px=[0,0,260,90])

@unittest.skipIf(Image is None,"optional Senate image dependency unavailable")
class SenateSegmentedDatesTests(unittest.TestCase):
    def test_two_source_slashes_and_complete_numeric_components(self):
        image,record=fixture();result=read(image,record)
        self.assertEqual(result["complete_date_text"],"7/9/26")
        self.assertEqual(result["confidence"],.99)
        self.assertEqual(len(record["geometry"]["slashes"]),2)

    def test_missing_extra_or_no_numeric_source_never_infers_slashes(self):
        for text in ("79/26","7//9/26","7 9 26","/ /"):
            with self.subTest(text=text):
                image,record=fixture(text);self.assertIsNone(read(image,record))

    def test_source_slot_pixels_geometry_and_numeric_crop_hash_are_bound(self):
        image,record=fixture()
        for field,value in [("page_sha256","b"*64),("grid_slot",2),("date_grayscale_sha256","0"*64)]:
            altered=deepcopy(record);altered[field]=value
            with self.assertRaises(SenateEfdError):read(image,altered)
        altered=deepcopy(record);altered["geometry"]["slashes"][0]["box_px"][0]+=1
        with self.assertRaises(SenateEfdError):read(image,altered)
        altered=deepcopy(record);altered["numeric_reads"][0]["input_pixels_sha256"]="0"*64
        with self.assertRaises(SenateEfdError):read(image,altered)
        changed=image.copy();changed.putpixel((20,35),(255,0,0))
        with self.assertRaises(SenateEfdError):read(changed,record)

    def test_crop_cannot_clip_one_source_pixel_or_cross_separator(self):
        image,record=fixture()
        for index,value in [(0,record["numeric_reads"][0]["box_px"][0]+1),(2,100)]:
            altered=deepcopy(record);altered["numeric_reads"][0]["box_px"][index]=value
            with self.assertRaisesRegex(SenateEfdError,"clips or crosses"):
                read(image,altered)

    def test_unreliable_or_nonnumeric_read_keeps_unknown(self):
        image,record=fixture()
        for text,confidence in [("B",.99),("9",.89),("9",float("nan")),("9",1.1)]:
            altered=deepcopy(record);altered["numeric_reads"][1].update(text=text,confidence=confidence)
            self.assertIsNone(read(image,altered))

    def _parse(self,raw="779/26",date="2026-07-09"):
        args=inputs(date=raw);args[3][0]["date_segment_observations"]=[{"page_number":2,"grid_slot":1}]
        with patch("unison_snapshot.senate_paper_dates.interpret_segmented_date",return_value={
                "complete_date_text":"7/9/26","confidence":.99,"source_observation":{},"transaction_date":date}):
            result=parse_paper_grid_observations(*args)
        return result

    def test_parser_preserves_old_raw_id_and_other_reasons(self):
        before=parse_paper_grid_observations(*inputs(date="779/26"))["transactions"][0]
        after=self._parse()["transactions"][0]
        self.assertEqual(before["extraction_id"],after["extraction_id"])
        self.assertEqual(after["field_observations"]["date_raw"],"779/26")
        self.assertEqual(self._parse()["date_segment_rule_version"],"senate-source-segmented-date/v1")
        self.assertEqual(after["transaction_date"],"2026-07-09")
        # Saved slot had an explicit differing valid original read, retained as conflict.
        self.assertIn("paper_date_observation_conflict",after["quarantine_reasons"])

    def test_valid_opposite_full_read_is_not_overwritten(self):
        after=self._parse(raw="07/08/26")["transactions"][0]
        self.assertEqual(after["transaction_date"],"2026-07-08")
        self.assertIn("paper_date_observation_conflict",after["quarantine_reasons"])

    def test_duplicate_or_outside_source_slot_read_is_rejected(self):
        for entries in [[{"page_number":2,"grid_slot":9}],
                        [{"page_number":2,"grid_slot":1},{"page_number":2,"grid_slot":1}]]:
            args=inputs();args[3][0]["date_segment_observations"]=entries
            with self.assertRaises(SenateEfdError):parse_paper_grid_observations(*args)

    def test_candidate_requires_controlled_replay_for_new_date_evidence(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unison_snapshot.senate_candidate import build_senate_candidate
        from tests.test_senate_candidate import identity,BASE,PARSER
        from tests import test_senate_candidate as helpers
        from tests.test_senate_paper import REPORT
        extraction=self._parse();extraction["parser_version"]=PARSER
        with TemporaryDirectory() as folder:
            root=Path(folder);state=helpers.SenateCandidateTests().fixture(root,[identity(REPORT["document_id"])],[extraction])
            with self.assertRaisesRegex(SenateEfdError,"archived source replay"):
                build_senate_candidate(root,state,deepcopy(BASE))
            with self.assertRaisesRegex(SenateEfdError,"replay pointer"):
                build_senate_candidate(root,state,deepcopy(BASE),evidence_root=root)

    def test_disconnected_strokes_reassemble_only_existing_ink(self):
        image,_=fixture();draw=ImageDraw.Draw(image)
        draw.rectangle((13,27,31,54),fill="white")
        draw.rectangle((14,28,30,31),fill="black")
        draw.line((23,34,17,53),fill="black",width=4)
        before=image.tobytes();plain=segment_geometry(image)
        joined=segment_geometry(image,"disconnected_strokes")
        self.assertEqual(plain["status"],"unknown")
        self.assertEqual(joined["status"],"two_source_slashes")
        self.assertEqual(image.tobytes(),before)
        self.assertEqual(len(joined["slashes"]),2)
        reads=[{"role":role,"box_px":box,"text":text,"confidence":.99} for role,box,text in zip(
            ["month","day","year"],joined["numeric_component_boxes_px"],["7","9","26"])]
        record=saved_segmented_date(image,page_sha256="a"*64,page_number=2,grid_slot=1,date_box_px=[0,0,260,90],
            reads=reads,engine={"name":"synthetic"},mode="disconnected_strokes")
        self.assertEqual(read(image,record)["complete_date_text"],"7/9/26")

    def test_prefix_requires_direct_explicit_second_slash_read_not_inferred(self):
        image,_=fixture();draw=ImageDraw.Draw(image)
        draw.line((65,40,85,40),fill="black",width=1)
        geometry=segment_geometry(image,"prefix_slash")
        self.assertEqual(geometry["status"],"source_prefix_slash")
        reads=[{"role":role,"box_px":box,"text":text,"confidence":.99} for role,box,text in zip(
            ["month","remainder"],geometry["numeric_component_boxes_px"],["7","9/26"])]
        record=saved_segmented_date(image,page_sha256="a"*64,page_number=2,grid_slot=1,date_box_px=[0,0,260,90],
            reads=reads,engine={"name":"synthetic"},mode="prefix_slash")
        self.assertEqual(read(image,record)["complete_date_text"],"7/9/26")
        for raw in ["926","9-26","9/2G","9/26/27"]:
            changed=deepcopy(record);changed["numeric_reads"][1]["text"]=raw
            self.assertIsNone(read(image,changed))
        _,ordinary=fixture()
        self.assertEqual(segment_geometry(image)["status"],"unknown")
        with self.assertRaises(SenateEfdError):segment_geometry(image,"approved_answer")
