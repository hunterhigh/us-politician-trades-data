"""Retained generic facts stay qualified only with reproducible source bindings."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_whitehouse_278t_generic import report, consume, ROW, SHA, DOC, URL
from test_whitehouse_278t_candidate import _base, _catalog
from unison_snapshot.oge_reports import parse_table_rows
from unison_snapshot.whitehouse_278t_candidate import _person, _transaction
from unison_snapshot.disclosure_status_oge import _record, _apply_audit


class WhiteHouseGenericStatusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.review = Path(self.directory.name)
        raw = ROW[:]
        raw[2], raw[-1] = "Purchase", "$1,001 - $15,000"
        oldrows, _ = parse_table_rows([(2, raw)], source_sha=SHA)
        self.legacy = {"schema_version": "whitehouse-278t-extraction/v1",
            "parser_version": "whitehouse-278t-hybrid-geometry/v2", "document_id": DOC,
            "source_url": URL, "source_sha256": SHA, "filer_name": "Wiles, Susie",
            "transactions": oldrows, "quarantined": []}
        self.report = report([(2, raw)], legacy=self.legacy)
        person = _person(_catalog()["reports"][0])
        base = _base()
        base["people"] = [person]
        previous = deepcopy(self.report)
        previous["parser_version"] = self.legacy["parser_version"]
        base["transactions"] = [_transaction(previous, oldrows[0], person["id"])]
        base["meta"]["data_cutoff_at"] = "2026-06-20T00:00:00Z"
        _, audit = consume(self.report, base)
        self.audit = audit["reports"][0]
        self.folder = self.review / "whitehouse/extractions" / DOC.split(":")[-1] / SHA
        self.folder.mkdir(parents=True)
        self.write("legacy.json", self.legacy)
        self.write("generic.json", self.report)

    def write(self, name, value):
        (self.folder / name).write_text(json.dumps(value), encoding="utf-8")

    def record(self):
        result = _record(DOC, URL, "278t", "current")
        result["source_versions"] = [SHA]
        return result

    def test_zero_new_rows_with_retained_facts_is_qualified(self):
        result = self.record()
        _apply_audit(result, self.audit, review=self.review)
        self.assertEqual(self.audit["promoted_count"], 0)
        self.assertEqual(result["qualified_transaction_count"], 1)
        self.assertEqual(result["qualification_state"], "qualified")
        self.assertEqual(result["qualified_retained_transaction_ids"], self.audit["retained_ids"])

    def test_retained_labels_cannot_replace_raw_source_binding(self):
        for change in ("count", "ids", "hash", "cell", "locator", "source"):
            audit = deepcopy(self.audit)
            if change == "count":
                audit["retained_count"] = 2
            elif change == "ids":
                audit["retained_ids"] = ["unbound"]
            elif change == "hash":
                audit["retained_source_bindings"]["legacy_extraction_canonical_sha256"] = "f" * 64
            elif change == "cell":
                audit["retained_source_bindings"]["rows"][0]["raw_cells_canonical_sha256"] = "f" * 64
            elif change == "locator":
                audit["retained_source_bindings"]["rows"][0]["source_rows_index"] = 99
            else:
                audit["source_sha256"] = "f" * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                _apply_audit(self.record(), audit, review=self.review)

    def test_cache_tampering_or_missing_legacy_is_not_qualification(self):
        changed = deepcopy(self.report)
        changed["transactions"][0]["asset_name"] = "EDITED"
        self.write("generic.json", changed)
        with self.assertRaises(ValueError):
            _apply_audit(self.record(), self.audit, review=self.review)
        self.write("generic.json", self.report)
        (self.folder / "legacy.json").unlink()
        with self.assertRaises(ValueError):
            _apply_audit(self.record(), self.audit, review=self.review)
