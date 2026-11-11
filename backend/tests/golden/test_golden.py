"""Byte-for-byte regression of the selected archived source reports."""
import unittest

from run import HERE, cases, replay


class GoldenReports(unittest.TestCase):
    def test_archived_reports(self):
        self.maxDiff = None
        for case in cases():
            with self.subTest(format=case['format'], report=case['id']):
                expected = (HERE / case['key'] / 'expected.json').read_bytes()
                self.assertEqual(expected.decode('utf-8'), replay(case).decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
