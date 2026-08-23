"""
Cross-source candidate matching — an OPTIONAL, explicitly separate feature.

This module is not used anywhere in the core /unified endpoints. It backs
only the new /match/<identifier> endpoint (see app/main.py). This
separation is deliberate — see DECISIONS.md, "Cross-source matching (added
as a separate, explicit feature)".

What this does, in plain terms: given one record from one source, search
every record in the OTHER source and score how likely each one is to
describe the same person. Always returns the single closest candidate and
its real score — even a low or zero score — so the caller can see exactly
how close (or not) the nearest thing was, rather than just "nothing
found." Deciding whether a score counts as a confident match is left to
the caller (see build_matched_view in core/unified.py), using
MATCH_THRESHOLD below.

This is a heuristic, not a guarantee. Two different people could share a
name and date of birth. The score is reported precisely so a human
reviewing the result can judge that risk themselves — this module never
claims certainty it doesn't have.

Scoring (out of 100, all fields optional so partial data still scores):
    - last name matches (case-insensitive)        : 40 points
    - first name matches (case-insensitive)        : 30 points
    - date of birth matches exactly                : 25 points
    - city matches (case-insensitive)              : 5 points
MATCH_THRESHOLD (70) is the bar the CALLER uses to decide whether a score
counts as a reportable match. This module itself doesn't hide anything
below it — it always hands back the real number.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

MATCH_THRESHOLD = 70  # out of 100 — last+first+dob (95) clears this; name-only (70) barely does, city alone never does


@dataclass
class MatchCandidate:
    record: dict
    score: int
    matched_on: list  # which fields contributed, for transparency


def _split_xml_name(name_raw: Optional[str]) -> tuple[str, str]:
    """XML source stores 'LASTNAME, First' as one string. Split it defensively."""
    if not name_raw or "," not in name_raw:
        return "", ""
    last, first = name_raw.split(",", 1)
    return last.strip(), first.strip()


def _score_pair(rest_record: dict, xml_record: dict) -> tuple[int, list]:
    """Score how likely a resident_index record and a benefits_register
    record describe the same person. Both records are already normalized
    (see adapters/*.py _normalize methods)."""
    score = 0
    matched_on = []

    xml_last, xml_first = _split_xml_name(xml_record.get("name_raw"))

    rest_last = (rest_record.get("last_name") or "").strip().lower()
    rest_first = (rest_record.get("first_name") or "").strip().lower()

    if rest_last and xml_last and rest_last == xml_last.lower():
        score += 40
        matched_on.append("last_name")

    if rest_first and xml_first and rest_first == xml_first.lower():
        score += 30
        matched_on.append("first_name")

    rest_dob = rest_record.get("date_of_birth")
    xml_dob = xml_record.get("date_of_birth")
    if rest_dob and xml_dob and rest_dob == xml_dob:
        score += 25
        matched_on.append("date_of_birth")

    rest_city = (rest_record.get("city") or "").strip().lower()
    xml_city = (xml_record.get("city") or "").strip().lower()
    if rest_city and xml_city and rest_city == xml_city:
        score += 5
        matched_on.append("city")

    return score, matched_on


def find_best_match(anchor_record: dict, anchor_source: str, candidates: list[dict]) -> Optional[MatchCandidate]:
    """
    anchor_record: the normalized record we already have (from either source)
    anchor_source: "resident_index" or "benefits_register" — which side the anchor is from
    candidates: the FULL normalized record list from the OTHER source

    Always returns the single highest-scoring candidate found, even if its
    score is low (or zero) — the caller decides what counts as a confident
    match using MATCH_THRESHOLD. We report the number rather than hide it:
    a score of 5 and "found nothing at all" mean different things to
    someone reading the result. Returns None only if candidates is empty.
    """
    best: Optional[MatchCandidate] = None

    for candidate in candidates:
        if anchor_source == "resident_index":
            score, matched_on = _score_pair(anchor_record, candidate)
        else:
            score, matched_on = _score_pair(candidate, anchor_record)

        if best is None or score > best.score:
            best = MatchCandidate(record=candidate, score=score, matched_on=matched_on)

    return best
