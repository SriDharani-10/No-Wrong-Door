"""
Adapter for Source 1 — the Resident Index (paginated REST, port 8081 by default).

Known behaviour we build around (see data pack README):
  - The index is ordered by `last_contact`, which other processes update
    while a client is paging. In practice this means the page boundary can
    slip, and the same record can be served on more than one page.
  - The service itself is reliable (no injected failures) — the only real
    hazard here is the duplicate-across-pages behaviour, not availability.

Strategy:
  - Page through /residents until has_more is false.
  - De-duplicate by `id` as pages arrive (a dict keyed by id, last write wins
    — since duplicates are the *same* record served twice, not conflicting
    versions, last-write-wins is safe and simple).
  - A handful of transient network retries on top, for parity with the other
    adapter and because "retry-safe" is a floor requirement for the whole
    API, not just the flaky source.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Optional

from adapters.base import SourceAdapter, SourceResult, SourceStatus

DEFAULT_TIMEOUT = 3.0
MAX_ATTEMPTS = 3


class ResidentIndexAdapter(SourceAdapter):
    name = "resident_index"

    def __init__(self, base_url: str = "http://127.0.0.1:8081", timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- low-level fetch with a small retry budget for transient network issues --
    def _get_json(self, path: str) -> tuple[Optional[dict], Optional[str], int]:
        last_err = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(f"{self.base_url}{path}", timeout=self.timeout) as resp:
                    body = resp.read()
                    return json.loads(body), None, attempt
            except urllib.error.HTTPError as e:
                # HTTP-level error response (e.g. 404) — not a connectivity
                # problem, so don't retry; let the caller interpret it.
                try:
                    parsed = json.loads(e.read())
                except Exception:
                    parsed = None
                return parsed, f"http_{e.code}", attempt
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = str(e)
                continue
        return None, f"unreachable: {last_err}", MAX_ATTEMPTS

    @staticmethod
    def _normalize(r: dict) -> dict:
        return {
            "source": "resident_index",
            "source_id": r.get("id"),
            "first_name": r.get("first_name"),
            "last_name": r.get("last_name"),
            "date_of_birth": r.get("date_of_birth"),
            "address": r.get("address_line"),
            "city": r.get("city"),
            "phone": r.get("phone"),
            "program_status": r.get("program_status"),
            "last_contact": r.get("last_contact"),
        }

    def health(self) -> SourceResult:
        data, err, attempts = self._get_json("/health")
        if err:
            return SourceResult(self.name, SourceStatus.UNAVAILABLE, reason=err, attempts=attempts)
        return SourceResult(self.name, SourceStatus.OK, data=data, attempts=attempts)

    def fetch_one(self, identifier: str) -> SourceResult:
        data, err, attempts = self._get_json(f"/residents/{identifier}")
        if err == "http_404":
            return SourceResult(self.name, SourceStatus.NOT_FOUND,
                                 reason="no record with this id", attempts=attempts)
        if err:
            return SourceResult(self.name, SourceStatus.UNAVAILABLE, reason=err, attempts=attempts)
        return SourceResult(self.name, SourceStatus.OK, data=self._normalize(data), attempts=attempts)

    def fetch_all(self) -> SourceResult:
        """Page through the full index, de-duplicating boundary repeats."""
        seen: dict[str, dict] = {}
        page = 1
        total_attempts = 0
        while True:
            data, err, attempts = self._get_json(f"/residents?page={page}")
            total_attempts += attempts
            if err:
                # We already have some pages — return what we collected,
                # clearly marked as a partial result, rather than discarding
                # everything gathered so far.
                if seen:
                    return SourceResult(
                        self.name, SourceStatus.DEGRADED,
                        data=list(seen.values()),
                        reason=f"paging stopped early at page {page}: {err}",
                        attempts=total_attempts,
                    )
                return SourceResult(self.name, SourceStatus.UNAVAILABLE, reason=err, attempts=total_attempts)

            for r in data.get("results", []):
                seen[r["id"]] = self._normalize(r)

            if not data.get("has_more"):
                break
            page += 1

        return SourceResult(self.name, SourceStatus.OK, data=list(seen.values()), attempts=total_attempts)
