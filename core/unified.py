"""
Assembly logic: turns one or more SourceResults into the unified view the
API returns. This module knows nothing about HTTP, XML, or pagination — it
only knows the SourceAdapter contract (adapters/base.py). That boundary is
deliberate: the day-two requirements change should be absorbable by editing
one adapter or this file, without the other having to change.

Degradation policy (also stated in DECISIONS.md — that file is the primary
record of this policy; this docstring mirrors it so the code and the
decision log can't drift apart):

  - A source that answers normally contributes its data, status "ok".
  - A source that answers after our internal retries contributes its data,
    status "degraded", with a reason ("succeeded after N attempts").
  - A source that fails after exhausting retries contributes no data for
    this call, status "unavailable", with a reason. It NEVER causes the
    whole request to fail — the caller gets everything that *was*
    available, plus an explicit, itemised account of what wasn't and why.
  - A source that is reachable but has no record for this person reports
    status "not_found" — distinct from "unavailable", because "we don't
    have you" and "we couldn't reach the system" mean different things to
    a caseworker.
  - Cross-source correlation (deciding two records describe the same
    person) is NOT attempted here. There is no shared key, and the problem
    statement is explicit that identity matching is a stretch goal, not
    part of the floor. The unified endpoint is keyed by an identifier the
    caller already has (from either source); it does not guess.
"""
from __future__ import annotations

import re
from dataclasses import asdict
from typing import Optional

from adapters.base import SourceAdapter, SourceResult, SourceStatus

REST_ID_PATTERN = re.compile(r"^R-\d+$")
XML_REF_PATTERN = re.compile(r"^[A-Za-z]{2}/\d{4}/\d+$")


def classify_identifier(identifier: str) -> Optional[str]:
    """Return 'resident_index', 'benefits_register', or None if unrecognised."""
    if REST_ID_PATTERN.match(identifier):
        return "resident_index"
    if XML_REF_PATTERN.match(identifier):
        return "benefits_register"
    return None


def _result_block(result: SourceResult) -> dict:
    block = {"status": result.status.value, "attempts": result.attempts}
    if result.reason:
        block["reason"] = result.reason
    return block


def build_unified_view(identifier: str, resident_adapter: SourceAdapter,
                        benefits_adapter: SourceAdapter) -> dict:
    """
    Build the unified view for one resident, identified by a value the
    caller already holds from EITHER source.

    We only ever query the source the identifier belongs to — we do not
    guess at a matching record in the other source. See module docstring.
    """
    origin = classify_identifier(identifier)

    view = {
        "requested_identifier": identifier,
        "resident_index": None,
        "benefits_register": None,
        "sources": {},
        "correlation": {
            "attempted": False,
            "note": (
                "The two source systems share no common key, and cross-source "
                "identity matching is out of scope for this endpoint (see "
                "DECISIONS.md). This view reflects only the source the "
                "supplied identifier belongs to."
            ),
        },
    }

    if origin is None:
        view["error"] = (
            "identifier not recognised as a Resident Index id (e.g. R-10234) "
            "or a Benefits Register ref (e.g. NO/2019/4234)"
        )
        return view

    if origin == "resident_index":
        result = resident_adapter.fetch_one(identifier)
        view["sources"]["resident_index"] = _result_block(result)
        view["sources"]["benefits_register"] = {
            "status": "not_queried",
            "reason": "identifier belongs to the Resident Index; no ref for the Benefits Register was supplied",
        }
        if result.ok():
            view["resident_index"] = result.data
        elif result.status == SourceStatus.NOT_FOUND:
            view["error"] = "no such resident in the Resident Index"
        else:
            view["error"] = "Resident Index unavailable for this request"
    else:
        result = benefits_adapter.fetch_one(identifier)
        view["sources"]["benefits_register"] = _result_block(result)
        view["sources"]["resident_index"] = {
            "status": "not_queried",
            "reason": "identifier belongs to the Benefits Register; no id for the Resident Index was supplied",
        }
        if result.ok():
            view["benefits_register"] = result.data
        elif result.status == SourceStatus.NOT_FOUND:
            view["error"] = "no such record in the Benefits Register"
        else:
            view["error"] = "Benefits Register unavailable for this request"

    return view


def build_unified_pair(resident_id: Optional[str], benefits_ref: Optional[str],
                        resident_adapter: SourceAdapter, benefits_adapter: SourceAdapter) -> dict:
    """
    Build a unified view from a KNOWN pair of identifiers — used when a
    caseworker already knows both ids for the same person (e.g. from
    existing casework) and wants both pulled together in one call. This is
    still not identity matching: the caller supplies the correlation, we
    don't infer it.
    """
    view = {
        "requested": {"resident_id": resident_id, "benefits_ref": benefits_ref},
        "resident_index": None,
        "benefits_register": None,
        "sources": {},
    }

    if resident_id:
        r = resident_adapter.fetch_one(resident_id)
        view["sources"]["resident_index"] = _result_block(r)
        if r.ok():
            view["resident_index"] = r.data
    else:
        view["sources"]["resident_index"] = {"status": "not_queried", "reason": "no resident id supplied"}

    if benefits_ref:
        b = benefits_adapter.fetch_one(benefits_ref)
        view["sources"]["benefits_register"] = _result_block(b)
        if b.ok():
            view["benefits_register"] = b.data
    else:
        view["sources"]["benefits_register"] = {"status": "not_queried", "reason": "no benefits ref supplied"}

    return view


def build_matched_view(identifier: str, resident_adapter, benefits_adapter) -> dict:
    """
    Attempt cross-source matching for one identifier — an explicitly
    separate, opt-in feature from build_unified_view above. Backs ONLY the
    /match/<identifier> endpoint (see app/main.py). build_unified_view
    itself is untouched and behaves exactly as before.

    Fetches the anchor record from the source the identifier belongs to
    (reusing the same adapters, same degradation handling as everywhere
    else), then searches the FULL listing of the other source for the
    best-scoring candidate (see core/matching.py). Reports the match score
    plainly — never silently merges without showing its work.
    """
    from core.matching import find_best_match, MATCH_THRESHOLD

    origin = classify_identifier(identifier)
    result = {
        "requested_identifier": identifier,
        "anchor": None,
        "anchor_source": origin,
        "match": None,
        "match_found": False,
        "sources": {},
    }

    if origin is None:
        result["error"] = (
            "identifier not recognised as a Resident Index id (e.g. R-10234) "
            "or a Benefits Register ref (e.g. NO/2019/4234)"
        )
        return result

    if origin == "resident_index":
        anchor_result = resident_adapter.fetch_one(identifier)
        other_adapter = benefits_adapter
        other_name = "benefits_register"
    else:
        anchor_result = benefits_adapter.fetch_one(identifier)
        other_adapter = resident_adapter
        other_name = "resident_index"

    result["sources"][origin] = _result_block(anchor_result)

    if not anchor_result.ok():
        result["error"] = f"{origin} unavailable or has no record for this identifier"
        return result

    result["anchor"] = anchor_result.data

    other_result = other_adapter.fetch_all()
    result["sources"][other_name] = _result_block(other_result)

    if not other_result.ok():
        result["error"] = f"{other_name} unavailable — cannot attempt matching right now"
        return result

    best = find_best_match(anchor_result.data, origin, other_result.data)
    if best is None:
        # only happens if the other source's full listing was genuinely empty
        result["match_found"] = False
        result["note"] = "the other source returned no records to compare against"
        return result

    result["match_found"] = best.score >= MATCH_THRESHOLD
    if best.score >= 90:
        confidence = "high"
    elif best.score >= MATCH_THRESHOLD:
        confidence = "medium"
    elif best.score > 0:
        confidence = "low — below the confidence threshold, shown for transparency only"
    else:
        confidence = "none — no fields in common with the closest candidate"

    result["match"] = {
        "record": best.record,
        "score": best.score,
        "confidence": confidence,
        "matched_on": best.matched_on,
        "threshold": MATCH_THRESHOLD,
    }
    if not result["match_found"]:
        result["note"] = (
            f"closest candidate scored {best.score}/100, below the {MATCH_THRESHOLD} "
            "confidence threshold — shown for reference, not reported as a confirmed match"
        )
    return result
