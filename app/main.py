#!/usr/bin/env python3
"""
No Wrong Door — the unified resident view API.

    python3 app/main.py [--port 8080]

Endpoints
    GET /health
        This service's own health, plus the reachability of both upstream
        sources. Always returns 200 — a degraded upstream is reported in
        the body, not via this endpoint's own status code, so a caller can
        tell "my proxy is up" from "the systems behind it are healthy"
        without the two being conflated.

    GET /residents
        Full, de-duplicated listing from the Resident Index (Source 1).
        Handles the page-boundary duplicate bug internally.

    GET /residents/<id>
        Single Resident Index record, e.g. /residents/R-10234

    GET /benefits
        Full listing from the Benefits Register (Source 2). Retries
        transient failures internally; if the source is down even after
        retries, returns what's available plus a clear error block rather
        than a bare 500.

    GET /benefits/<ref>
        Single Benefits Register record, e.g. /benefits/NO%2F2019%2F4234
        (the ref contains slashes — URL-encode it)

    GET /unified/<identifier>
        THE core endpoint. Accepts EITHER a Resident Index id or a
        Benefits Register ref and returns everything known from that
        source, plus an explicit statement about the other source (see
        DECISIONS.md — cross-source matching is a stretch goal, not part
        of this call).

    GET /unified?resident_id=R-10234&benefits_ref=NO/2019/4234
        Same idea, but for when the caller already knows BOTH identifiers
        for the same person (e.g. from prior casework) and wants a single
        combined pull. Still not identity matching — the correlation is
        supplied by the caller, not inferred.

All responses are JSON. All failure modes documented in DECISIONS.md.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.resident_index import ResidentIndexAdapter
from adapters.benefits_register import BenefitsRegisterAdapter
from core.unified import build_unified_view, build_unified_pair, classify_identifier

RESIDENT_INDEX_URL = os.environ.get("RESIDENT_INDEX_URL", "http://127.0.0.1:8081")
BENEFITS_REGISTER_URL = os.environ.get("BENEFITS_REGISTER_URL", "http://127.0.0.1:8082")

resident_adapter = ResidentIndexAdapter(base_url=RESIDENT_INDEX_URL)
benefits_adapter = BenefitsRegisterAdapter(base_url=BENEFITS_REGISTER_URL)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"

        try:
            if path == "/health":
                return self._handle_health()

            if path == "/residents":
                return self._handle_residents_all()

            if path.startswith("/residents/"):
                rid = unquote(path[len("/residents/"):])
                return self._handle_residents_one(rid)

            if path == "/benefits":
                return self._handle_benefits_all()

            if path.startswith("/benefits/"):
                ref = unquote(path[len("/benefits/"):])
                return self._handle_benefits_one(ref)

            if path == "/unified":
                rid = q.get("resident_id", [None])[0]
                bref = q.get("benefits_ref", [None])[0]
                if not rid and not bref:
                    return self._send(400, {
                        "error": "supply resident_id and/or benefits_ref as query params, "
                                 "or use /unified/<identifier> with a single known id"
                    })
                view = build_unified_pair(rid, bref, resident_adapter, benefits_adapter)
                return self._send(200, view)

            if path.startswith("/unified/"):
                identifier = unquote(path[len("/unified/"):])
                view = build_unified_view(identifier, resident_adapter, benefits_adapter)
                code = 404 if view.get("error", "").startswith("no such") else 200
                if view.get("error", "").startswith("identifier not recognised"):
                    code = 400
                return self._send(code, view)

            return self._send(404, {"error": "no_such_endpoint", "path": path})

        except Exception as e:  # last line of defence: never let the process die on a bad request
            return self._send(500, {"error": "internal_error", "detail": str(e)})

    # -- handlers --------------------------------------------------------

    def _handle_health(self):
        r_health = resident_adapter.health()
        b_health = benefits_adapter.health()
        return self._send(200, {
            "service": "no-wrong-door-api",
            "status": "ok",
            "upstreams": {
                "resident_index": {"status": r_health.status.value, "reason": r_health.reason},
                "benefits_register": {"status": b_health.status.value, "reason": b_health.reason},
            },
        })

    def _handle_residents_all(self):
        result = resident_adapter.fetch_all()
        return self._send(200 if result.ok() else 502, {
            "status": result.status.value,
            "reason": result.reason,
            "count": len(result.data) if result.data else 0,
            "results": result.data or [],
        })

    def _handle_residents_one(self, rid: str):
        result = resident_adapter.fetch_one(rid)
        if result.status.value == "not_found":
            return self._send(404, {"status": "not_found", "reason": result.reason})
        if not result.ok():
            return self._send(502, {"status": result.status.value, "reason": result.reason})
        return self._send(200, {"status": result.status.value, "result": result.data})

    def _handle_benefits_all(self):
        result = benefits_adapter.fetch_all()
        return self._send(200 if result.ok() else 502, {
            "status": result.status.value,
            "reason": result.reason,
            "count": len(result.data) if result.data else 0,
            "results": result.data or [],
        })

    def _handle_benefits_one(self, ref: str):
        result = benefits_adapter.fetch_one(ref)
        if result.status.value == "not_found":
            return self._send(404, {"status": "not_found", "reason": result.reason})
        if not result.ok():
            return self._send(502, {"status": result.status.value, "reason": result.reason})
        return self._send(200, {"status": result.status.value, "result": result.data})

    def log_message(self, fmt, *a):
        print(f"  [api] {fmt % a}")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    print(f"No Wrong Door API on http://127.0.0.1:{args.port}")
    print(f"  Resident Index  -> {RESIDENT_INDEX_URL}")
    print(f"  Benefits Register -> {BENEFITS_REGISTER_URL}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
