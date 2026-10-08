import unittest

from unison_snapshot.pipeline_qa.g3_shadow_gate import compare_bundle_tree, git_blob_oid


class G3ShadowGateTests(unittest.TestCase):
    def test_all_bundle_files_must_match_published_tree(self):
        files = {"manifest.json": b"one", "board/example.json": b"two"}
        tree = {path: git_blob_oid(content) for path, content in files.items()}
        self.assertTrue(compare_bundle_tree(files, tree)["all_candidate_files_match_published_main"])
        tree["manifest.json"] = git_blob_oid(b"changed")
        report = compare_bundle_tree(files, tree)
        self.assertFalse(report["all_candidate_files_match_published_main"])
        self.assertEqual(report["mismatched_paths"], ["manifest.json"])

    def test_missing_release_file_is_a_mismatch(self):
        self.assertEqual(compare_bundle_tree({"manifest.json": b"x"}, {})["mismatched_paths"],
                         ["manifest.json"])


if __name__ == "__main__":
    unittest.main()
