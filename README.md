# No Wrong Door — unified resident view API

Brite Spark 2026 · Problem 3 · Integration / Engineering

A single API that returns everything known about a resident, assembled from
two independent, unreliable backend systems — without ever handing the
caller a bare error when one of them is having a bad day (which, per the
data pack, is normal for one of them).

## Requirements

Python 3.9+. Standard library only — nothing to install, no `pip install`,
no virtual environment needed for the core system.

## Running it

From the repo root, in three terminals (or use the helper script, which
does this for you):

```bash
./scripts/run_all.sh
```

This starts, in order: the Resident Index (REST, :8081), the Benefits
Register (XML, :8082), then this API (:8080). Ctrl-C stops all three.

Or manually:

```bash
python3 services/rest_service.py --port 8081 &
python3 services/xml_service.py  --port 8082 &
python3 app/main.py --port 8080
```

Then:

```bash
curl http://127.0.0.1:8080/health
```

## Endpoints

| Method & path | What it does |
|---|---|
| `GET /health` | This API's health, plus reachability of both upstream sources |
| `GET /residents` | Full, de-duplicated listing from the Resident Index |
| `GET /residents/<id>` | One Resident Index record, e.g. `/residents/R-10234` |
| `GET /benefits` | Full listing from the Benefits Register |
| `GET /benefits/<ref>` | One Benefits Register record, e.g. `/benefits/NO%2F2019%2F4234` (URL-encode the slashes) |
| `GET /unified/<identifier>` | **The core endpoint.** Pass an id from *either* source; get back everything known from that source, plus an explicit statement about the other |
| `GET /unified?resident_id=..&benefits_ref=..` | Same idea, for when you already know both identifiers for one person |

See each endpoint's docstring in `app/main.py` for full detail, and
**`DECISIONS.md`** for the degradation policy — what a caller gets when a
source is slow, down, or simply doesn't have the record, and how they can
tell those apart.

## Try it

The exact ids/refs below are examples from this data pack's generated data
(both systems are seeded fresh each run, so if you regenerate the data
these specific ids won't match — use `/residents` or `/benefits` first to
grab any current id/ref from your own run).

```bash
# a resident that exists
curl http://127.0.0.1:8080/unified/R-10234

# a benefits record that exists (note the slash is URL-encoded as %2F)
curl http://127.0.0.1:8080/unified/NO%2F2019%2F4664

# both known identifiers for the same person, pulled together
curl "http://127.0.0.1:8080/unified?resident_id=R-10234&benefits_ref=NO/2019/4664"

# a benefits ref that does NOT exist, to see the honest "not_found" path
# (distinct from "unavailable" — see DECISIONS.md)
curl http://127.0.0.1:8080/unified/ZZ%2F1900%2F0000
```

Run `curl http://127.0.0.1:8080/benefits` a handful of times in a row, or
repeat the second command above — the Benefits Register fails ~15% of
calls and takes 0.7–2.4s per call by design (see the data pack). You'll
see `"status": "degraded"` when a retry was needed and
`"status": "unavailable"` (with a reason, never a bare 500) on the rare
case all retries are exhausted.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

These run against fake adapters and a synthetic paginator — no live
services required. They cover: the pagination de-duplication bug, the
degradation contract (ok / degraded / unavailable / not_found are always
distinguished), and identifier classification.

## Project layout

```
adapters/            One module per source system. Each knows only its own
                      HTTP/XML quirks and returns a normalized SourceResult
                      — never raises for routine failures.
core/unified.py       Assembly logic. Combines SourceResults into the
                      unified view. Knows nothing about HTTP or XML.
app/main.py           The HTTP API. Wires adapters + assembly together.
services/              The two mock backend systems, exactly as provided.
tests/                 Unit tests (no live services needed).
scripts/run_all.sh     Convenience: brings up all three processes.
DECISIONS.md           What we chose, rejected, cut, and why. Start here.
AI-USAGE.md             What AI tooling was used and for what.
```

## What this deliberately does not do

- **No cross-source identity matching.** The two sources share no key,
  and the problem statement is explicit that this is a stretch goal, not
  the floor. `/unified/<id>` reports the source the identifier came from
  and says plainly it hasn't tried to guess the other. See DECISIONS.md.
- **No UI.** Not assessed on this problem.
- **No database.** Both sources are read on demand; nothing is persisted.
- **No auth.** Not in scope per the problem document.
