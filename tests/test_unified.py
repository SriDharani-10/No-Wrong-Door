"""
Unit tests that don't require the mock services to be running — they test
the assembly logic and identifier classification directly against fake
adapters. For a full end-to-end check against the real mock services, see
tests/test_live.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.base import SourceAdapter, SourceResult, SourceStatus
from core.unified import build_unified_view, build_unified_pair, classify_identifier


class FakeAdapter(SourceAdapter):
    """Lets tests script exactly what a source returns, without a network."""

    def __init__(self, name, one_result=None, health_result=None):
        self.name = name
        self._one = one_result
        self._health = health_result or SourceResult(name, SourceStatus.OK)

    def health(self):
        return self._health

    def fetch_one(self, identifier):
        return self._one

    def fetch_all(self):
        raise NotImplementedError


class TestClassifyIdentifier(unittest.TestCase):
    def test_rest_id(self):
        self.assertEqual(classify_identifier("R-10234"), "resident_index")

    def test_xml_ref(self):
        self.assertEqual(classify_identifier("NO/2019/4234"), "benefits_register")

    def test_unrecognised(self):
        self.assertIsNone(classify_identifier("not-an-id"))
        self.assertIsNone(classify_identifier(""))
        self.assertIsNone(classify_identifier("R10234"))  # missing hyphen


class TestUnifiedViewDegradation(unittest.TestCase):
    """
    These tests encode the floor requirement directly: a source failure
    must never produce a bare error for the whole request, and the
    response must always say, explicitly, what happened to each source.
    """

    def test_ok_resident_lookup_does_not_query_benefits(self):
        resident = FakeAdapter("resident_index", SourceResult(
            "resident_index", SourceStatus.OK, data={"source_id": "R-1"}))
        benefits = FakeAdapter("benefits_register")  # would raise if called
        view = build_unified_view("R-1", resident, benefits)
        self.assertEqual(view["resident_index"], {"source_id": "R-1"})
        self.assertIsNone(view["benefits_register"])
        self.assertEqual(view["sources"]["benefits_register"]["status"], "not_queried")
        self.assertFalse(view["correlation"]["attempted"])

    def test_degraded_source_still_returns_data_and_says_why(self):
        resident = FakeAdapter("resident_index", SourceResult(
            "resident_index", SourceStatus.DEGRADED, data={"source_id": "R-1"},
            reason="succeeded after 2 attempts", attempts=2))
        benefits = FakeAdapter("benefits_register")
        view = build_unified_view("R-1", resident, benefits)
        self.assertEqual(view["resident_index"], {"source_id": "R-1"})
        self.assertEqual(view["sources"]["resident_index"]["status"], "degraded")
        self.assertIn("2 attempts", view["sources"]["resident_index"]["reason"])

    def test_unavailable_source_never_produces_a_bare_failure(self):
        resident = FakeAdapter("resident_index")
        benefits = FakeAdapter("benefits_register", SourceResult(
            "benefits_register", SourceStatus.UNAVAILABLE, reason="http_500", attempts=3))
        view = build_unified_view("NO/2019/4234", resident, benefits)
        self.assertIsNone(view["benefits_register"])
        self.assertEqual(view["sources"]["benefits_register"]["status"], "unavailable")
        self.assertEqual(view["sources"]["benefits_register"]["reason"], "http_500")
        # The view itself is still a well-formed object, not an exception/crash.
        self.assertIn("error", view)

    def test_not_found_is_distinct_from_unavailable(self):
        resident = FakeAdapter("resident_index", SourceResult(
            "resident_index", SourceStatus.NOT_FOUND, reason="no record with this id"))
        benefits = FakeAdapter("benefits_register")
        view = build_unified_view("R-99999", resident, benefits)
        self.assertEqual(view["sources"]["resident_index"]["status"], "not_found")
        self.assertNotEqual(view["sources"]["resident_index"]["status"], "unavailable")

    def test_unrecognised_identifier_queries_nothing(self):
        resident = FakeAdapter("resident_index")   # would raise if called
        benefits = FakeAdapter("benefits_register")  # would raise if called
        view = build_unified_view("garbage", resident, benefits)
        self.assertIn("error", view)
        self.assertEqual(view["sources"], {})


class TestUnifiedPair(unittest.TestCase):
    def test_both_supplied_both_queried(self):
        resident = FakeAdapter("resident_index", SourceResult(
            "resident_index", SourceStatus.OK, data={"source_id": "R-1"}))
        benefits = FakeAdapter("benefits_register", SourceResult(
            "benefits_register", SourceStatus.OK, data={"source_id": "NO/1"}))
        view = build_unified_pair("R-1", "NO/1", resident, benefits)
        self.assertEqual(view["resident_index"], {"source_id": "R-1"})
        self.assertEqual(view["benefits_register"], {"source_id": "NO/1"})

    def test_one_omitted_is_not_queried_not_unavailable(self):
        resident = FakeAdapter("resident_index", SourceResult(
            "resident_index", SourceStatus.OK, data={"source_id": "R-1"}))
        benefits = FakeAdapter("benefits_register")  # would raise if called
        view = build_unified_pair("R-1", None, resident, benefits)
        self.assertEqual(view["sources"]["benefits_register"]["status"], "not_queried")


if __name__ == "__main__":
    unittest.main()
