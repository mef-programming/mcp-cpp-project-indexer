from __future__ import annotations

import unittest

from benchmarks.benchmark_orientation_incremental import compare_incremental_updates


class OrientationIncrementalBenchmarkTests(unittest.TestCase):
    def test_synthetic_reuse_and_rebuild_produce_equal_indexes(self) -> None:
        report = compare_incremental_updates(documents=4, runs=1)
        self.assertTrue(report["equivalent"])
        self.assertEqual(report["fixtureDocuments"], 4)
        self.assertEqual(len(report["samples"]["reuse"]["aggregationMs"]), 1)
        self.assertEqual(len(report["samples"]["rebuild"]["aggregationMs"]), 1)


if __name__ == "__main__":
    unittest.main()
