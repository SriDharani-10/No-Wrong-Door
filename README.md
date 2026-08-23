# No Wrong Door — unified resident view API

Brite Spark 2026 · Problem 3 · Integration / Engineering

A single API that returns everything known about a resident, assembled from
two independent, unreliable backend systems — without ever handing the
caller a bare error when one of them is having a bad day (which, per the
data pack, is normal for one of them).

## Requirements

Python 3.9+. Standard library only — nothing to install, no `pip install`,
no virtual environment needed for the core system.

**Note:** the Benefits Register runs at a 40% failure rate by default,
reflecting the day-two requirement change (see DECISIONS.md, "Day two").

## Running it

## Clone it

```bash
git clone https://github.com/SriDharani-10/No-Wrong-Door.git
cd No-Wrong-Door
```

From the repo root, in three terminals (or use the helper script, which
does this for you):

```bash
./scripts/run_all.sh
```

This starts, in order: the Resident Index (REST, :8081), the Benefits
Register (XML, :8082), then this API (:8080). Ctrl-C stops all three.

Or manually:
(Windows users: run the 3 commands below separately, each in its own terminal/cmd window)

```bash
python3 services/rest_service.py --port 8081 &
python3 services/xml_service.py  --port 8082 --failure-rate 0.40 &
python3 app/main.py --port 8080
```

Windows users: use `python` instead of `python3` in the above commands.

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
| `GET /match/<identifier>` | **Actively searches the other source for a likely match** (name + date of birth + city) and reports a confidence score, or honestly reports no confident match. See DECISIONS.md, "Cross-source matching." |
| `GET /unified/<identifier>` | Pass an id from *either* source; returns everything known from **that source only**, plus an explicit statement that the other source was not queried (no guessing — see "What this deliberately does not do" below) |
| `GET /unified?resident_id=..&benefits_ref=..` | Same as above, but for when you already know both identifiers for one person and want both pulled together in one call |

See each endpoint's docstring in `app/main.py` for full detail, and
**`DECISIONS.md`** for the degradation policy — what a caller gets when a
source is slow, down, or simply doesn't have the record, and how they can
tell those apart.

## Try it

The exact ids/refs below are examples from this data pack's generated data
(the data is static and seeded, so these should exist on any machine
running this same data pack — if not, use `/residents` or `/benefits`
first to grab any current id/ref from your own run).

### 1. Matching — search for a person automatically, using only one id

```bash
# give ONE known id; the system searches the OTHER source itself and
# scores the best candidate it finds
curl http://127.0.0.1:8080/match/R-10063
```
**What this does:** fetches Elena Ashford (R-10063) from the Resident
Index, then searches every Benefits Register record for the closest
match on name, date of birth, and city.
**Expected output:** a `"match"` block showing `CA/2023/4063`, `"score": 100`,
`"confidence": "high"`, and `"matched_on"` listing all four fields —
found automatically, with no second id supplied.

```bash
# a resident with a real but non-matching counterpart in the other source
curl http://127.0.0.1:8080/match/R-10491
```
**Expected output:** `"match_found": false`, but the closest candidate is
still shown with its real score (e.g. `"score": 45`) and which fields did
and didn't match — never a bare "nothing found."

### 2. Unified — pull data from one specific source only, no guessing

```bash
# a resident that exists in the Resident Index
curl http://127.0.0.1:8080/unified/R-10234
```
**What this does:** looks at the shape of the id, recognises it as a
Resident Index id, and fetches **only** from that source.
**Expected output:** full resident data under `"resident_index"`, and
`"benefits_register": null` with `"status": "not_queried"` — this is
correct and expected, not a failure. `/unified` never searches the other
source on its own; that's what `/match` is for.

```bash
# a benefits record that exists (note the slash is URL-encoded as %2F)
curl http://127.0.0.1:8080/unified/NO%2F2019%2F4664
```
**Expected output:** the mirror case — full data under
`"benefits_register"`, `"resident_index": null` with `"status": "not_queried"`.

```bash
# both known identifiers for the same person, pulled together in one call
curl "http://127.0.0.1:8080/unified?resident_id=R-10234&benefits_ref=NO/2019/4234"
```
**What this does:** for when a caseworker already knows both ids for one
person (e.g. from prior casework) and wants both combined in a single
response — still not identity matching, since both ids are supplied by
the caller, not guessed.
**Expected output:** both `"resident_index"` and `"benefits_register"`
filled in if both ids are valid; `"not_found"` on whichever side has a
made-up id.

```bash
# a benefits ref that does NOT exist, to see the honest "not_found" path
curl http://127.0.0.1:8080/unified/ZZ%2F1900%2F0000
```
**Expected output:** `"status": "not_found"`, `"reason": "no record with
this ref"` — distinct from `"unavailable"` (see DECISIONS.md).

### 3. Watching the Benefits Register degrade live

```bash
curl http://127.0.0.1:8080/benefits
```
Run this a handful of times in a row, or repeat any `/match` or `/unified`
command against a Benefits Register id — the source fails ~40% of calls and takes 0.7–2.4s per call by design (raised from 15% on day two — see DECISIONS.md, "Day two") You'll see
`"status": "degraded"` when a retry was needed and `"status":
"unavailable"` (with a reason, never a bare 500) on the rare case all
retries are exhausted.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

These run against fake adapters and a synthetic paginator — no live
services required. They cover: the pagination de-duplication bug, the
degradation contract (ok / degraded / unavailable / not_found are always
distinguished), matching score logic, and identifier classification.

## Project layout
```
adapters/ One module per source system. Each knows only its own
HTTP/XML quirks and returns a normalized SourceResult
— never raises for routine failures.
core/unified.py Assembly logic. Combines SourceResults into the
unified view. Knows nothing about HTTP or XML.
core/matching.py Cross-source candidate scoring. Optional feature,
used only by /match — completely separate from the
required /unified endpoints.
app/main.py The HTTP API. Wires adapters + assembly together.
services/ The two mock backend systems, exactly as provided.
tests/ Unit tests (no live services needed).
scripts/run_all.sh Convenience: brings up all three processes.
DECISIONS.md What we chose, rejected, cut, and why. Start here.
AI-USAGE.md What AI tooling was used and for what.

```
## What this deliberately does not do

- **`/unified` never guesses at a cross-source match.** The two sources
  share no key, and the problem statement is explicit that automatic
  identity matching is a stretch goal, not the floor. `/unified/<id>`
  reports the source the identifier came from and says plainly it hasn't
  tried to guess the other. The optional `/match/<id>` endpoint, added
  afterward, does attempt this — see DECISIONS.md for its scoring
  approach and honest limitations.
- **No UI.** Not assessed on this problem.
- **No database.** Both sources are read on demand; nothing is persisted.
- **No auth.** Not in scope per the problem document.