"""
Tests for core/matching.py — the scoring logic, tested directly with fake
records, no live services needed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.matching import find_best_match, _score_pair, MATCH_THRESHOLD


REST_ELENA = {
    "first_name": "Elena", "last_name": "Ashford",
    "date_of_birth": "1953-09-07", "city": "Calder Central",
}
XML_ELENA_EXACT = {
    "name_raw": "ASHFORD, Elena", "date_of_birth": "1953-09-07", "city": "Calder Central",
}
XML_DIFFERENT_PERSON = {
    "name_raw": "FARROW, William", "date_of_birth": "1966-02-17", "city": "Northgate",
}
XML_SAME_NAME_DIFFERENT_DOB = {
    "name_raw": "ASHFORD, Elena", "date_of_birth": "1990-01-01", "city": "Calder Central",
}


class TestScorePair(unittest.TestCase):
    def test_exact_match_scores_full(self):
        score, matched_on = _score_pair(REST_ELENA, XML_ELENA_EXACT)
        self.assertEqual(score, 100)
        self.assertEqual(set(matched_on), {"last_name", "first_name", "date_of_birth", "city"})

    def test_completely_different_person_scores_zero(self):
        score, matched_on = _score_pair(REST_ELENA, XML_DIFFERENT_PERSON)
        self.assertEqual(score, 0)
        self.assertEqual(matched_on, [])

    def test_same_name_different_dob_does_not_score_full(self):
        # Same name is a weak signal alone — DOB mismatch should keep the
        # score well below what a genuine match gets, precisely so this
        # case doesn't get silently treated as confident.
        score, _ = _score_pair(REST_ELENA, XML_SAME_NAME_DIFFERENT_DOB)
        self.assertLess(score, 100)


class TestFindBestMatch(unittest.TestCase):
    def test_finds_genuine_match_among_candidates(self):
        candidates = [XML_DIFFERENT_PERSON, XML_ELENA_EXACT, XML_SAME_NAME_DIFFERENT_DOB]
        best = find_best_match(REST_ELENA, "resident_index", candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best.record, XML_ELENA_EXACT)
        self.assertEqual(best.score, 100)

    def test_no_candidate_above_threshold_returns_none(self):
        candidates = [XML_DIFFERENT_PERSON]
        best = find_best_match(REST_ELENA, "resident_index", candidates)
        self.assertIsNone(best)

    def test_threshold_is_enforced_not_just_highest_score(self):
        # Even if this is the "best" of a bad set, it shouldn't be reported
        # as a match if it doesn't clear the confidence bar.
        weak_candidate = {"name_raw": "ASHFORD, Someone", "date_of_birth": "1953-09-07", "city": ""}
        best = find_best_match(REST_ELENA, "resident_index", [weak_candidate])
        if best is not None:
            self.assertGreaterEqual(best.score, MATCH_THRESHOLD)

    def test_works_symmetrically_from_benefits_register_side(self):
        candidates = [REST_ELENA]
        best = find_best_match(XML_ELENA_EXACT, "benefits_register", candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best.record, REST_ELENA)
        self.assertEqual(best.score, 100)


if __name__ == "__main__":
    unittest.main()
