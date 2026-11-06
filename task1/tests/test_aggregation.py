"""Offline aggregation regression tests for the historical candle comparator."""
import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "code" / "collect_compare.py"
spec = importlib.util.spec_from_file_location("candle_compare", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

BASE = 1793804400000  # 2026-11-04 15:00:00 UTC


def child(offset):
    return {
        "time_ms": BASE + offset * 60_000,
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100.5"),
        "volume": Decimal("10.25"),
        "count": offset + 2,
    }


class CandleAggregationTests(unittest.TestCase):
    def setUp(self):
        self.children = [child(i) for i in range(5)]
        self.higher = dict(module.aggregate(self.children), time_ms=BASE)

    def test_aggregate_adds_event_counts(self):
        self.assertEqual(module.aggregate(self.children)["count"], 20)

    def test_complete_bucket_matches_count(self):
        result = module.compare(self.children, [self.higher], 300, 60, BASE, BASE + 300_000)
        self.assertEqual(result[0]["status"], "MATCH")
        self.assertEqual(result[0]["expected_count"], "20")
        self.assertEqual(result[0]["actual_count"], "20")

    def test_count_mismatch_is_detected(self):
        wrong = dict(self.higher, count=21)
        result = module.compare(self.children, [wrong], 300, 60, BASE, BASE + 300_000)
        self.assertEqual(result[0]["status"], "DIFFERENCE")
        self.assertEqual(result[0]["different_fields"], "count")

    def test_duplicate_child_timestamp_is_incomplete(self):
        duplicated = self.children + [dict(self.children[0])]
        result = module.compare(duplicated, [self.higher], 300, 60, BASE, BASE + 300_000)
        self.assertEqual(result[0]["status"], "INCOMPLETE_LOWER_INTERVAL")

    def test_missing_child_timestamp_is_incomplete(self):
        result = module.compare(self.children[:-1], [self.higher], 300, 60, BASE, BASE + 300_000)
        self.assertEqual(result[0]["status"], "INCOMPLETE_LOWER_INTERVAL")


if __name__ == "__main__":
    unittest.main()
