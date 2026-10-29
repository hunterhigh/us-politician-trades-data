"""Constructed ID/status semantics; no real same-ID predecessor is claimed."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from test_house_ptr import amended_fixture_pages,META,IDENTITY
from unison_snapshot.house import HouseIndexError
from unison_snapshot.house_ptr import parse_word_pages,qualify_automatic
from unison_snapshot.house_candidate import build_house_candidate
from test_house_native_account_context import fixture
from test_house_candidate import BASE


class HouseOfficialTransactionIdSemanticsTests(unittest.TestCase):
    def source(self,status="New",identifier="2000140446"):
        pages=amended_fixture_pages()
        for word in pages[0]["words"]:
            if word["text"]=="Amended":word["text"]=status
            if word["text"]=="2000140446":word["text"]=identifier
        return parse_word_pages(META,"a"*64,pages,copy_allowed=True)

    def test_numeric_id_alone_does_not_make_explicit_new_status_an_amendment(self):
        x=self.source();q=qualify_automatic(x,IDENTITY)
        self.assertEqual(len(q["transactions"]),1)
        self.assertEqual(q["transactions"][0]["id"],x["transactions"][0]["extraction_id"])
        self.assertNotIn("revision_observations",q)
        self.assertEqual(x["transactions"][0]["reported_transaction_id"],"2000140446")
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);state=fixture(root,x,q)
            candidate=build_house_candidate(root,state,deepcopy(BASE))
            self.assertEqual(candidate["transactions"],q["transactions"])
            self.assertEqual(build_house_candidate(root,state,candidate),candidate)

    def test_amended_status_still_requires_source_bound_predecessor(self):
        q=qualify_automatic(self.source("Amended"),IDENTITY)
        self.assertEqual(q["transactions"],[])
        self.assertIn("revision_relationship_unresolved",q["quarantined"][0]["reasons"])
        self.assertEqual(q["revision_observations"][0]["reported_transaction_id"],"2000140446")

    def test_invalid_identifier_is_not_accepted_even_without_parser_problem_label(self):
        x=self.source();x["transactions"][0]["reported_transaction_id"]="unknown"
        # Saved native observations now reject altered fields before row rules.
        with self.assertRaises(HouseIndexError):
            qualify_automatic(x,IDENTITY)
        # Truly older caches have no saved words; independent row checks must
        # still reject the malformed ID without relying on parser labels.
        x.pop("source_word_pages");x.pop("source_word_pages_sha256")
        q=qualify_automatic(x,IDENTITY)
        self.assertEqual(q["transactions"],[])
        self.assertIn("reported_transaction_id_invalid",q["quarantined"][0]["reasons"])


if __name__=="__main__":unittest.main()
