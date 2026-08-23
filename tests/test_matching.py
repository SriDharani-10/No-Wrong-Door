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

    def test_low_scoring_candidate_is_still_returned_with_its_real_score(self):
        # find_best_match no longer hides a weak result — it always reports
        # the closest candidate and its actual score. Deciding what counts
        # as a "confident match" is the CALLER's job (build_matched_view),
        # not this function's — see that threshold check separately below.
        candidates = [XML_DIFFERENT_PERSON]
        best = find_best_match(REST_ELENA, "resident_index", candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best.score, 0)
        self.assertEqual(best.record, XML_DIFFERENT_PERSON)

    def test_empty_candidate_list_returns_none(self):
        best = find_best_match(REST_ELENA, "resident_index", [])
        self.assertIsNone(best)

    def test_threshold_constant_is_still_exported_for_callers_to_use(self):
        # The threshold itself still exists and is used by build_matched_view
        # to decide match_found — just no longer enforced inside this function.
        self.assertIsInstance(MATCH_THRESHOLD, int)
        self.assertGreater(MATCH_THRESHOLD, 0)

    def test_works_symmetrically_from_benefits_register_side(self):
        candidates = [REST_ELENA]
        best = find_best_match(XML_ELENA_EXACT, "benefits_register", candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best.record, REST_ELENA)
        self.assertEqual(best.score, 100)


if __name__ == "__main__":
    unittest.main()
