"""
Shared contract for source adapters.

Every adapter (one per backend system) implements this interface and nothing
more. The assembly layer (core/unified.py) only ever talks to adapters
through this contract — it never knows whether a source is REST, XML, or
anything else. That separation is deliberate: it is what lets one source's
behaviour change without the rest of the system caring (see DECISIONS.md,
"day two").

Every adapter call returns a SourceResult. Adapters never raise for ordinary
failure modes (timeouts, 500s, malformed responses) — those are expected,
routine conditions for these sources, not exceptions. Adapters only let
genuinely unexpected errors (bugs) propagate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SourceStatus(str, Enum):
    OK = "ok"                  # call succeeded, data is trustworthy
    DEGRADED = "degraded"      # call succeeded after retries, or partial data
    UNAVAILABLE = "unavailable"  # call failed after exhausting retries
    NOT_FOUND = "not_found"    # source reachable, record does not exist there


@dataclass
class SourceResult:
    """The outcome of asking one adapter for one thing."""
    source: str                      # stable adapter name, e.g. "resident_index"
    status: SourceStatus
    data: Optional[Any] = None       # normalized record(s), or None
    reason: Optional[str] = None     # human-readable explanation, required when not OK
    attempts: int = 1                # how many calls it took (for transparency)

    def ok(self) -> bool:
        return self.status in (SourceStatus.OK, SourceStatus.DEGRADED)


class SourceAdapter:
    """Interface every source adapter must implement."""

    name: str = "base"

    def health(self) -> SourceResult:
        raise NotImplementedError

    def fetch_one(self, identifier: str) -> SourceResult:
        raise NotImplementedError

    def fetch_all(self) -> SourceResult:
        raise NotImplementedError
