"""Selection must retain shared checks and never omit a full-run test module."""
import importlib.util
from pathlib import Path
import unittest

PATH=Path(__file__).resolve().parents[1]/'scripts/verify.py'
SPEC=importlib.util.spec_from_file_location('verification_selector',PATH)
selector=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(selector)


class VerificationSelectionTests(unittest.TestCase):
    def test_full_shards_partition_the_entire_suite_without_duplicates(self):
        expected=set(selector.selected_modules())
        shards=[set(selector.selected_modules(shard=i,shards=4)) for i in range(4)]
        self.assertEqual(set.union(*shards),expected)
        self.assertEqual(sum(map(len,shards)),len(expected))
        self.assertEqual(expected,{'.'.join(p.relative_to(selector.BACKEND/'tests').with_suffix('').parts)
            for p in (selector.BACKEND/'tests').rglob('test_*.py')})

    def test_source_changes_keep_shared_checks_and_oge_whitehouse_coupling(self):
        modules=selector.selected_modules(['backend/src/unison_snapshot/house_ptr.py'])
        self.assertIn('test_house_candidate',modules)
        self.assertIn('test_corrections',modules)
        self.assertNotIn('test_senate_candidate',modules)
        self.assertEqual(selector.affected_sources(['backend/data/corrections/whitehouse/report.json']),{'oge','whitehouse'})
        self.assertEqual(selector.affected_sources(['backend/src/unison_snapshot/corrections.py']),selector.SOURCES)
        self.assertEqual(selector.affected_sources(['.github/actions/data-trees/action.yml']),selector.SOURCES)
        self.assertEqual(selector.affected_sources(['docs/status.md']),set())


if __name__=='__main__':unittest.main()
