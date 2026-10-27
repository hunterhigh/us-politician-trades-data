"""Actual generated PDFs/ZIP/XML roots for context, not migration permission."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pymupdf

from test_house import fixture as index_zip
from test_house_members import XML
from test_house_ptr import amended_fixture_pages
from unison_snapshot.house import archive_discovery, archive_document, parse_index
from unison_snapshot.house_members import archive_members, parse_members, suggest_identity
from unison_snapshot.house_ptr import parse_archived_pdf, qualify_automatic
from unison_snapshot.house_revision_source_context import load_house_revision_source_context


PERSON = "house:D000032"
TIME = "2026-09-18T00:00:00Z"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def read(path):
    return json.loads(path.read_bytes())


def member(doc, *, first="Byron", last="Donalds", district="FL19", day=20):
    return (f"<Member><Prefix>Hon.</Prefix><First>{first}</First><Last>{last}</Last>"
        f"<FilingType>P</FilingType><StateDst>{district}</StateDst><Year>2026</Year>"
        f"<FilingDate>9/{day}/2026</FilingDate><DocID>{doc}</DocID></Member>")


def native_pdf(document_id, *, status="New", numbered=True, image_page=False):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for item in amended_fixture_pages()[0]["words"]:
        text = item["text"]
        if text == "#20000001":
            text = "#" + document_id
        if text == "Amended":
            text = status
        if text == "2000140446" and not numbered:
            continue
        page.insert_text((item["x0"], item["top"] + item["size"]), text, fontsize=item["size"])
    if image_page:
        raster = pymupdf.open()
        rp = raster.new_page(width=612, height=792)
        rp.insert_text((25, 334), "2000140446", fontsize=9)
        pixels = rp.get_pixmap(alpha=False)
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=pixels.tobytes("png"))
        raster.close()
    content = doc.tobytes()
    doc.close()
    return content


class HouseRevisionSourceContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hctx-")
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name) / "e"
        self.review = Path(self.temp.name) / "r"
        self.evidence.mkdir()
        self.review.mkdir()
        published, members = parse_members(XML)
        meta = archive_members(self.evidence, XML, {}, members, published, retrieved_at=TIME)
        self.roster = {"metadata": meta, "members": [asdict(m) for m in members]}
        self.rows = member("20000001", day=10) + member("20000002", day=15)
        self.index = self.archive_index(self.rows)
        self.metas, self.extractions, self.qualifications = {}, {}, {}
        for doc, status in (("20000001", "New"), ("20000002", "Amended")):
            self.add_document(doc, status=status)

    def archive_index(self, records, timestamp=TIME):
        raw = index_zip(records)
        return archive_discovery(self.evidence, 2026, raw, {}, parse_index(raw, 2026), retrieved_at=timestamp)

    def add_document(self, doc, *, status="New", numbered=True, image_page=False, cache=True):
        filing = next(r for r in parse_index(index_zip(self.rows), 2026) if r.document_id == doc)
        meta = archive_document(self.evidence, filing,
            native_pdf(doc, status=status, numbered=numbered, image_page=image_page), {}, retrieved_at=TIME)
        self.metas[doc] = meta
        if cache:
            extraction = parse_archived_pdf(self.evidence, (self.evidence / meta["archive_path"]).with_suffix(".json"))
            qualification = qualify_automatic(extraction, suggest_identity(extraction, self.roster))
            self.extractions[doc], self.qualifications[doc] = extraction, qualification
            relative = f"2026/{doc}/{meta['sha256']}.json"
            write(self.review / "house_clerk/extractions" / relative, extraction)
            write(self.review / "house_clerk/qualifications" / relative, qualification)
        return meta

    def load(self, **kwargs):
        return load_house_revision_source_context(self.evidence, self.review,
            requested_person_ids=[PERSON], prior_transactions=self.qualifications["20000001"]["transactions"], **kwargs)

    def document_record(self, context, doc):
        return next(r for r in context.for_person(PERSON)["sources"] if r["key"] == "2026/" + doc)

    def test_context_is_original_bound_repeatable_and_not_scope_or_projection_permission(self):
        before = {p.relative_to(self.temp.name).as_posix(): p.read_bytes() for p in Path(self.temp.name).rglob("*") if p.is_file()}
        context = self.load()
        self.assertEqual(context.status, "observed_context")
        self.assertFalse(context.scope_complete)
        self.assertFalse(context.review_projection_proven)
        with self.assertRaises(AttributeError):
            context.scope_complete = True
        self.assertEqual(context.provenance()["scope_years"], [2026])
        self.assertEqual(context.provenance()["selected_index_sha_by_year"], {"2026": self.index["sha256"]})
        self.assertEqual([r["key"] for r in context.for_person(PERSON)["sources"]], ["2026/20000001", "2026/20000002"])
        self.assertEqual(context.provenance(), self.load().provenance())
        document = context.documents_for(PERSON)[0]
        self.assertEqual(hashlib.sha256(document.pdf_bytes).hexdigest(), document.source_sha256)
        document.metadata["filer_name"] = "changed copy"
        context.provenance()["people"].clear()
        self.assertEqual(document.metadata["filer_name"], "Hon. Byron Donalds")
        self.assertIsNotNone(context.for_person(PERSON))
        after = {p.relative_to(self.temp.name).as_posix(): p.read_bytes() for p in Path(self.temp.name).rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_same_person_index_source_is_included_even_when_every_cached_id_is_erased(self):
        self.archive_index(self.rows + member("20000003"), "2026-09-19T00:00:00Z")
        for doc in self.extractions:
            relative = f"2026/{doc}/{self.metas[doc]['sha256']}.json"
            extraction = deepcopy(self.extractions[doc]); extraction["transactions"] = []
            qualification = deepcopy(self.qualifications[doc]); qualification["transactions"] = []
            write(self.review / "house_clerk/extractions" / relative, extraction)
            write(self.review / "house_clerk/qualifications" / relative, qualification)
        context = self.load()
        self.assertEqual(context.status, "unknown")
        self.assertEqual(len(context.for_person(PERSON)["sources"]), 3)
        self.assertIn("source_archive_missing_or_multiple_versions", self.document_record(context, "20000003")["reasons"])

    def test_another_xml_resolved_person_is_excluded_without_pdf_but_unknown_is_not(self):
        extra = (b"<member><statedistrict>NY01</statedistrict><member-info><bioguideID>C000001</bioguideID>"
            b"<official-name>Ada Carter</official-name><party>D</party><state postal-code='NY'/></member-info></member>")
        xml = XML.replace(b"</members>", extra + b"</members>")
        date, members = parse_members(xml)
        archive_members(self.evidence, xml, {}, members, date, retrieved_at=TIME)
        self.archive_index(self.rows + member("20000003", first="Ada", last="Carter", district="NY01"), "2026-09-19T00:00:00Z")
        context = self.load()
        self.assertEqual(context.status, "observed_context")
        self.assertEqual(context.for_person(PERSON)["excluded_sources"], [{"key": "2026/20000003", "person_id": "house:C000001"}])
        self.archive_index(self.rows + member("20000003", first="Unknown", last="Filer", district="ZZ00"), "2026-09-20T00:00:00Z")
        self.assertIn("index_person_membership_unknown:2026/20000003", self.load().reasons)

    def test_index_latest_tie_stale_state_and_removed_historical_key_never_fall_back(self):
        old = self.index["sha256"]
        self.archive_index(member("20000001", day=10), "2026-09-19T00:00:00Z")
        context = self.load(state_status={"filing_year": 2026, "index_sha256": old})
        self.assertIn("state_index_selection_conflicts_with_latest_source", context.reasons)
        self.assertIn("historical_only_index_key_not_disposed", self.document_record(context, "20000002")["reasons"])
        self.archive_index(self.rows + member("20000003"), "2026-09-19T00:00:00Z")
        context = self.load()
        self.assertIn("index_latest_retrieval_ambiguous:2026", context.reasons)
        self.assertNotIn("2026", context.provenance()["selected_index_sha_by_year"])

    def test_main_or_current_report_year_missing_index_is_not_an_unseen_empty_year(self):
        fact = deepcopy(self.qualifications["20000001"]["transactions"][0])
        fact["source_url"] = fact["source_url"].replace("/2026/", "/2025/")
        context = self.load(current_transactions=[fact])
        self.assertEqual(context.provenance()["scope_years"], [2025, 2026])
        self.assertIn("required_year_index_not_selected:2025", context.reasons)
        self.assertEqual(list((self.evidence / "house_clerk/index").iterdir()), [self.evidence / "house_clerk/index/2026"])

    def test_roster_conflict_and_self_signed_q_identity_cannot_choose_the_source_person(self):
        relative = f"2026/20000001/{self.metas['20000001']['sha256']}.json"
        qualification = deepcopy(self.qualifications["20000001"])
        qualification["identity"]["person_id"] = "house:D999999"
        write(self.review / "house_clerk/qualifications" / relative, qualification)
        context = self.load()
        self.assertIn("qualification_source_identity_binding_mismatch", self.document_record(context, "20000001")["reasons"])
        xml = XML.replace(b"D000032", b"D999999")
        date, members = parse_members(xml)
        archive_members(self.evidence, xml, {}, members, date, retrieved_at=TIME)
        context = self.load()
        self.assertIn("index_person_membership_unknown:2026/20000001", context.reasons)
        self.assertEqual(context.for_person(PERSON)["sources"], [])

    def test_all_canonical_metadata_fields_are_bound_to_index_and_original_bytes(self):
        path = (self.evidence / self.metas["20000001"]["archive_path"]).with_suffix(".json")
        original = read(path)
        mutations = {"source_id": "other", "filer_name": "Hon. Other Person", "state_district": "FL20",
            "filing_year": 2025, "filed_date": "2026-09-11", "filing_type": "O", "document_id": "20000009",
            "source_url": "https://example.invalid/source.pdf", "sha256": "a" * 64,
            "archive_path": "../../outside.pdf", "byte_length": original["byte_length"] + 1}
        for field, value in mutations.items():
            with self.subTest(field=field):
                write(path, {**original, field: value})
                context = self.load()
                self.assertEqual(context.status, "unknown")
                self.assertIn("canonical_source_metadata_binding_mismatch", self.document_record(context, "20000001")["reasons"])
        write(path, original)

    def test_missing_caches_and_multiple_original_versions_are_explicit_unknowns(self):
        for kind in ("extractions", "qualifications"):
            path = self.review / f"house_clerk/{kind}/2026/20000001/{self.metas['20000001']['sha256']}.json"
            raw = path.read_bytes(); path.unlink()
            context = self.load()
            self.assertIn(f"{kind}:source_object_missing", self.document_record(context, "20000001")["reasons"])
            path.write_bytes(raw)
        filing = parse_index(index_zip(self.rows), 2026)[0]
        raw = (self.evidence / self.metas["20000001"]["archive_path"]).read_bytes()
        archive_document(self.evidence, filing, raw + b"\n", {}, retrieved_at=TIME)
        context = self.load()
        self.assertIn("source_archive_missing_or_multiple_versions", self.document_record(context, "20000001")["reasons"])

    def test_actual_opaque_page_blocks_regardless_of_cached_ids_without_calling_ocr(self):
        self.rows += member("20000003")
        self.archive_index(self.rows, "2026-09-19T00:00:00Z")
        self.add_document("20000003", image_page=True, cache=False)
        with patch("unison_snapshot.house_ptr._ocr_pdf_pages", side_effect=AssertionError("OCR must not execute")):
            context = self.load()
        record = self.document_record(context, "20000003")
        self.assertEqual(record["actual_planned_ocr_pages"], [2])
        self.assertIn("opaque_source_cannot_prove_competitor_absence", record["reasons"])
        self.assertEqual(len(context.documents_for(PERSON)), 3)
        self.assertEqual(self.qualifications["20000001"]["transactions"], context.documents_for(PERSON)[0].qualification["transactions"])

    def test_unnumbered_new_financial_fact_stays_good_but_cannot_close_revision_context(self):
        self.rows += member("20000003")
        self.archive_index(self.rows, "2026-09-19T00:00:00Z")
        self.add_document("20000003", numbered=False)
        qualification = self.qualifications["20000003"]
        self.assertEqual(len(qualification["transactions"]), 1)
        context = self.load()
        self.assertIn("source_row_official_id_unavailable", self.document_record(context, "20000003")["reasons"])
        self.assertEqual(qualification, context.documents_for(PERSON)[2].qualification)
        self.assertFalse(context.scope_complete)

    def test_aggregate_budget_and_missing_native_dependencies_do_not_become_absence(self):
        with patch("unison_snapshot.house_revision_source_context.MAX_TOTAL_BYTES", 1):
            context = self.load()
        self.assertEqual(context.status, "unknown")
        self.assertIn("source_total_byte_budget_exceeded", context.reasons)
        with patch("unison_snapshot.house_revision_source_context.MAX_PAGES", 0):
            context = self.load()
        self.assertIn("source_page_budget_exceeded", self.document_record(context, "20000001")["reasons"])

    def test_unindexed_archive_or_review_key_remains_unknown_instead_of_disappearing(self):
        # Create a real canonical archive/cache source without adding it to any
        # official index snapshot. Its q identity cannot manufacture inclusion
        # or an independent exclusion for the unknown source.
        self.rows += member("20000003")
        self.add_document("20000003")
        context = self.load()
        self.assertEqual(context.status, "unknown")
        self.assertEqual(context.provenance()["unindexed_root_source_keys"], ["2026/20000003"])
        self.assertIn("root_source_not_bound_to_any_archived_index", context.reasons)

    def test_missing_roots_are_unknown_and_an_empty_request_grants_nothing(self):
        context = load_house_revision_source_context(None, None, requested_person_ids=[PERSON])
        self.assertEqual(context.status, "unknown")
        self.assertEqual(context.reasons, ["controlled_source_roots_unavailable"])
        empty = load_house_revision_source_context(None, None, requested_person_ids=[])
        self.assertEqual(empty.status, "observed_context")
        self.assertFalse(empty.scope_complete)
        self.assertEqual(empty.documents_for(PERSON), ())


if __name__ == "__main__":
    unittest.main()
