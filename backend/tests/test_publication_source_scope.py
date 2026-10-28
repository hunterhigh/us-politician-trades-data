import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import json
import unittest


spec = importlib.util.spec_from_file_location("publication_source_scope",
    Path(__file__).resolve().parents[1] / "src/unison_snapshot/commands/publication_source_scope.py")
scope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scope)


class SourceScopeTests(unittest.TestCase):
    def test_filtered_fetch_never_downloads_excluded_blobs_and_keeps_other_worktrees_full(self):
        with tempfile.TemporaryDirectory(prefix="publication-scope-") as folder:
            root = Path(folder).resolve()
            origin, client = root / "origin", root / "client"
            origin.mkdir()
            def git(cwd, *args):
                return subprocess.check_output(["git", "-C", str(cwd), *args],
                    stderr=subprocess.PIPE, text=True, encoding="utf-8").strip()
            git(origin, "init")
            files = {
                "whitehouse/ocr-checkpoints/a.json": b"parser checkpoint",
                "oge/catalog/pages/a.json": b"raw source page",
                "oge/catalog/normalized/a.json": b"normalized source",
                "candidates/sources/oge-current.json": b"qualified facts",
                "status/oge.json": b"processing status",
                "new-source/future.json": b"future input",
                "house_clerk/documents/2026/123/a.pdf": b"House PTR original",
                "house_clerk/documents/2026/123/a.json": b"House PTR receipt",
                "house_clerk/financial-reports/2026/124/a.pdf": b"House annual original",
                "house_clerk/financial-reports/2026/124/a.json": b"House annual receipt",
                "oge/reports/doc/a.pdf": b"OGE transaction original",
                "oge/reports/doc/a.json": b"OGE transaction receipt",
                "oge/annual/reports/doc/a.pdf": b"OGE annual original",
                "oge/annual/reports/doc/a.json": b"OGE annual receipt",
                "whitehouse/disclosures/reports/doc/a.pdf": b"White House original",
                "whitehouse/disclosures/reports/doc/a.part-000.bin": b"White House original chunk",
                "whitehouse/disclosures/reports/doc/a.json": b"White House receipt",
                "senate_efd/reports/doc/a.pdf": b"Senate archive existence input",
                "future-source/new.pdf": b"unknown future original",
            }
            for path, raw in files.items():
                target = origin / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            git(origin, "add", ".")
            git(origin, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "-m", "source fixture")
            commit = git(origin, "rev-parse", "HEAD")
            git(origin, "config", "uploadpack.allowFilter", "true")
            # Match Actions: start with a normal initialized repository, then
            # convert the source fetch to a filtered promisor fetch.
            excluded = {
                "review": {"whitehouse/ocr-checkpoints/a.json"},
                "evidence": {"oge/catalog/pages/a.json",
                    "house_clerk/documents/2026/123/a.pdf",
                    "house_clerk/financial-reports/2026/124/a.pdf",
                    "oge/reports/doc/a.pdf", "oge/annual/reports/doc/a.pdf",
                    "whitehouse/disclosures/reports/doc/a.pdf",
                    "whitehouse/disclosures/reports/doc/a.part-000.bin"},
            }
            for kind in ("review", "evidence"):
                # Separate clients prove each scope's omitted blobs were never
                # fetched, rather than hidden after another checkout read them.
                client = root / ("client-" + kind)
                client.mkdir(); git(client, "init")
                git(client, "remote", "add", "origin", origin.as_uri())
                git(client, "fetch", "--depth=1", "--filter=blob:none", "origin", commit)
                worktree = root / kind
                git(client, "worktree", "add", "--detach", "--no-checkout", str(worktree), commit)
                result = scope.apply_scope(worktree, kind)
                self.assertEqual(result["omitted_files"], len(excluded[kind]))
                self.assertEqual(result["omitted_blobs_already_present"], 0)
                for path, raw in files.items():
                    if path in excluded[kind]:
                        self.assertFalse((worktree / path).exists())
                    else:
                        self.assertEqual((worktree / path).read_bytes(), raw)
                self.assertEqual(git(worktree, "status", "--porcelain"), "")
                # Restore inputs needed by the new parser's status replay through
                # its existing CLI flag. v1 House consumers also replay original PDFs.
                output = subprocess.check_output([sys.executable, str(Path(scope.__file__)),
                    "--root", str(worktree), "--kind", kind, "--keep-parser-inputs"],
                    text=True, encoding="utf-8")
                parser_result = json.loads(output.splitlines()[-1])
                remaining = set()
                self.assertEqual(parser_result["profile"], "parser-safe")
                self.assertEqual(parser_result["omitted_files"], len(remaining))
                self.assertEqual(parser_result["omitted_blobs_already_present"], 0)
                self.assertEqual(parser_result["selected_bytes"],
                    sum(len(raw) for path, raw in files.items() if path not in remaining))
                for path, raw in files.items():
                    if path in remaining:
                        self.assertFalse((worktree / path).exists())
                    else:
                        self.assertEqual((worktree / path).read_bytes(), raw)
                full = root / ("full-" + kind)
                git(client, "worktree", "add", "--detach", str(full), commit)
                self.assertTrue(all((full / p).read_bytes() == raw for p, raw in files.items()))
                self.assertTrue(all(not (worktree / p).exists() for p in remaining))
                self.assertEqual(git(worktree, "status", "--porcelain"), "")


    def test_parser_safe_cli_restores_replay_bytes_and_leaves_other_worktrees_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="parser-safe-scope-") as folder:
            root = Path(folder).resolve()
            repo = root / "repo"
            repo.mkdir()
            def git(cwd, *args):
                return subprocess.check_output(["git", "-C", str(cwd), *args],
                    stderr=subprocess.PIPE, text=True, encoding="utf-8").strip()
            git(repo, "init")
            files = {
                "whitehouse/ocr-checkpoints/doc/saved.json": b'{"pages":[{"words":[]}]}',
                "oge/catalog/pages/source.json": b'{"data":[],"recordsTotal":0}',
                "oge/catalog/metadata.json": b'{"pages":[{"archive_path":"oge/catalog/pages/source.json"}]}',
                "candidates/sources/oge-current.json": b'{"transactions":[]}',
            }
            for path, raw in files.items():
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            git(repo, "add", ".")
            git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "replay inputs")
            commit = git(repo, "rev-parse", "HEAD")
            untouched = root / "untouched"
            git(repo, "worktree", "add", "--detach", str(untouched), commit)
            for kind in ("review", "evidence"):
                selected = root / kind
                git(repo, "worktree", "add", "--detach", "--no-checkout", str(selected), commit)
                # The unchanged default still omits the upstream collection paths.
                result = scope.apply_scope(selected, kind)
                self.assertEqual(result["profile"], "publication")
                self.assertEqual(result["omitted_files"], 1)
                output = subprocess.check_output([sys.executable, str(Path(scope.__file__)),
                    "--root", str(selected), "--kind", kind, "--keep-parser-inputs"],
                    text=True, encoding="utf-8")
                result = json.loads(output.splitlines()[-1])
                self.assertEqual(result["profile"], "parser-safe")
                self.assertEqual(result["omitted_files"], 0)
                self.assertEqual(result["selected_files"], len(files))
                self.assertEqual(result["selected_bytes"], sum(map(len, files.values())))
                self.assertEqual(git(selected, "rev-parse", "HEAD"), commit)
                self.assertEqual(git(selected, "status", "--porcelain"), "")
                self.assertTrue(all((selected / p).read_bytes() == raw for p, raw in files.items()))
                self.assertTrue(all((untouched / p).read_bytes() == raw for p, raw in files.items()))
                self.assertEqual(git(untouched, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
