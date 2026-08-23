"""
Adapter for Source 2 — the Benefits Register (legacy XML, port 8082 by default).

Known behaviour we build around (see data pack README and problem doc):
  - Slow: ~0.7-2.4s per call, always. Not a fault.
  - Unreliable: returns HTTP 500 on a fraction of calls. Not a fault either.
  - /health is deliberately exempt from both — it is the one clean way to
    tell "the service is down" from "this particular call failed."

Strategy:
  - Retry failed calls a small, bounded number of times with a short backoff.
    This is a background/legacy system with real load on it; retrying
    aggressively would make things worse, not better, so the budget is
    intentionally conservative (3 attempts, capped total wait).
  - If retries are exhausted, return UNAVAILABLE with a clear reason. Never
    fabricate data and never crash the caller — the assembly layer decides
    what a caller sees, this adapter just reports honestly.
  - GET requests are naturally idempotent here: the register is read-only
    from our side, so retrying a read never double-writes or duplicates
    anything. (There are no writes in this problem — see DECISIONS.md for
    what "retry-safe and idempotent" means concretely for a read-only API.)
"""
from __future__ import annotations

import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from typing import Optional

from adapters.base import SourceAdapter, SourceResult, SourceStatus

DEFAULT_TIMEOUT = 4.0       # generous: this source is *known* to take up to ~2.4s
MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.3          # seconds; small linear backoff between retries


class BenefitsRegisterAdapter(SourceAdapter):
    name = "benefits_register"

    def __init__(self, base_url: str = "http://127.0.0.1:8082", timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get_xml(self, path: str) -> tuple[Optional[ET.Element], Optional[str], int]:
        """
        Day-two update: the Benefits Register's failure rate rose to ~40%
        permanently (see DECISIONS.md, "Day two"). At that rate, calling
        one attempt at a time back-to-back pushed the worst-case wait for
        one call to ~7s (3 attempts x up to 2.4s each). To keep the floor's
        response times reasonable without weakening the retry guarantee,
        each retry "round" below now fires up to 2 attempts AT THE SAME
        TIME and uses whichever answers first — cutting the odds of a
        round failing from 40% to roughly 16% (0.4 x 0.4), without paying
        for the two attempts sequentially. Rounds still happen up to
        MAX_ATTEMPTS times with backoff between them, unchanged from
        before; only how each individual round is attempted changed.
        """
        import concurrent.futures

        def _one_call():
            with urllib.request.urlopen(f"{self.base_url}{path}", timeout=self.timeout) as resp:
                return ET.fromstring(resp.read())

        last_err = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_one_call), pool.submit(_one_call)]
                result = None
                errors = []
                for f in concurrent.futures.as_completed(futures):
                    try:
                        result = f.result()
                        break  # first success wins; the other attempt is left to finish quietly
                    except urllib.error.HTTPError as e:
                        errors.append(f"http_{e.code}")
                    except ET.ParseError as e:
                        errors.append(f"malformed_xml: {e}")
                    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                        errors.append(f"unreachable: {e}")

            if result is not None:
                return result, None, attempt

            last_err = errors[0] if errors else "both parallel attempts failed"
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_BASE * attempt)
                continue
            return None, last_err, attempt

        return None, last_err, MAX_ATTEMPTS
            

    @staticmethod
    def _text(el: Optional[ET.Element], tag: str) -> Optional[str]:
        child = el.find(tag) if el is not None else None
        return child.text if child is not None else None

    def _normalize(self, el: ET.Element) -> dict:
        return {
            "source": "benefits_register",
            "source_id": self._text(el, "Ref"),
            "name_raw": self._text(el, "Name"),       # "LASTNAME, First" — legacy format, deliberately not split here
            "date_of_birth": self._text(el, "Born"),
            "address": self._text(el, "Addr"),
            "city": self._text(el, "Town"),
            "benefit_code": self._text(el, "BenefitCode"),
            "review_due": self._text(el, "ReviewDue"),
        }

    def health(self) -> SourceResult:
        # /health is exempt from delay/failure injection by design — use it
        # as-is, no retry needed.
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=self.timeout) as resp:
                ET.fromstring(resp.read())
            return SourceResult(self.name, SourceStatus.OK)
        except Exception as e:
            return SourceResult(self.name, SourceStatus.UNAVAILABLE, reason=str(e))

    def fetch_one(self, identifier: str) -> SourceResult:
        root, err, attempts = self._get_xml(f"/records/{identifier}")
        if err == "http_404":
            return SourceResult(self.name, SourceStatus.NOT_FOUND,
                                 reason="no record with this ref", attempts=attempts)
        if err:
            return SourceResult(self.name, SourceStatus.UNAVAILABLE, reason=err, attempts=attempts)
        rec = root.find("Record")
        if rec is None:
            return SourceResult(self.name, SourceStatus.NOT_FOUND, reason="no record with this ref", attempts=attempts)
        status = SourceStatus.OK if attempts == 1 else SourceStatus.DEGRADED
        reason = None if attempts == 1 else f"succeeded after {attempts} attempts"
        return SourceResult(self.name, status, data=self._normalize(rec), reason=reason, attempts=attempts)

    def fetch_all(self) -> SourceResult:
        root, err, attempts = self._get_xml("/records")
        if err:
            return SourceResult(self.name, SourceStatus.UNAVAILABLE, reason=err, attempts=attempts)
        records = [self._normalize(rec) for rec in root.findall("Record")]
        status = SourceStatus.OK if attempts == 1 else SourceStatus.DEGRADED
        reason = None if attempts == 1 else f"succeeded after {attempts} attempts"
        return SourceResult(self.name, status, data=records, reason=reason, attempts=attempts)
