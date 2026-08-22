"""
Tests the Resident Index adapter's de-duplication against a fake HTTP layer
that reproduces the real service's boundary-slip bug (same record served on
consecutive pages), without needing the actual service running.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.resident_index import ResidentIndexAdapter
from adapters.base import SourceStatus


def make_pages_with_boundary_duplicates():
    """3 pages of 3 records, with the last record of page 1 repeated as the
    first record of page 2 — the exact bug described in the data pack."""
    return [
        {"page": 1, "has_more": True, "results": [
            {"id": "R-1", "first_name": "A"},
            {"id": "R-2", "first_name": "B"},
            {"id": "R-3", "first_name": "C"},
        ]},
        {"page": 2, "has_more": True, "results": [
            {"id": "R-3", "first_name": "C"},  # duplicate of page 1's last record
            {"id": "R-4", "first_name": "D"},
            {"id": "R-5", "first_name": "E"},
        ]},
        {"page": 3, "has_more": False, "results": [
            {"id": "R-6", "first_name": "F"},
        ]},
    ]


class TestResidentIndexDedup(unittest.TestCase):
    def test_fetch_all_deduplicates_boundary_repeats(self):
        pages = make_pages_with_boundary_duplicates()
        adapter = ResidentIndexAdapter()

        def fake_get_json(path):
            page_num = int(path.split("page=")[1])
            return pages[page_num - 1], None, 1

        with patch.object(adapter, "_get_json", side_effect=fake_get_json):
            result = adapter.fetch_all()

        self.assertEqual(result.status, SourceStatus.OK)
        ids = [r["source_id"] for r in result.data]
        self.assertEqual(len(ids), 6, "expected exactly 6 unique residents, not one per raw row")
        self.assertEqual(sorted(ids), ["R-1", "R-2", "R-3", "R-4", "R-5", "R-6"])

    def test_fetch_all_returns_partial_data_on_mid_page_failure(self):
        pages = make_pages_with_boundary_duplicates()
        adapter = ResidentIndexAdapter()
        call_count = {"n": 0}

        def flaky_get_json(path):
            call_count["n"] += 1
            page_num = int(path.split("page=")[1])
            if page_num == 3:
                return None, "unreachable: simulated", 3
            return pages[page_num - 1], None, 1

        with patch.object(adapter, "_get_json", side_effect=flaky_get_json):
            result = adapter.fetch_all()

        # Page 3 failed, but pages 1-2 succeeded — we should get those 5
        # records back, marked degraded, never a bare failure discarding
        # everything already collected.
        self.assertEqual(result.status, SourceStatus.DEGRADED)
        self.assertEqual(len(result.data), 5)
        self.assertIn("page 3", result.reason)


if __name__ == "__main__":
    unittest.main()
