# Decisions

Running log. Entries are added as choices are made, not reconstructed at
the end.

## Stack

Python 3, standard library only, for the API itself — same constraint the
mock services are built under. No Flask/FastAPI, no pip install. Reasoning:
the floor requires "runs from a clean clone using the README alone." Zero
dependencies means zero ways for that to fail because of an environment
difference between our machine and the judge's. This is a CLI/API problem,
not a UI problem (confirmed in the problem doc — command-line demonstration
is explicitly fine), so there's nothing to gain from a heavier framework.

## Architecture: adapters + assembly, strictly separated

`adapters/resident_index.py` and `adapters/benefits_register.py` each know
only their own source's quirks (pagination, XML parsing, retry/backoff).
Neither imports the other. `core/unified.py` only talks to adapters through
a shared contract (`adapters/base.py`: `SourceResult`, `SourceStatus`) and
knows nothing about HTTP or XML.

This is the direct answer to "the change is coming on day two, and you
won't be told what it is." We don't know what will change, so we optimized
for *where a change would land* rather than guessing its shape:

- A new/replaced source → one new adapter file, assembly layer untouched.
- A change to one source's failure behaviour → one adapter file touched.
- A change to what "unified" means (e.g. a third source, a different
  identifier scheme) → `core/unified.py`, adapters untouched.

We did not build speculative flexibility beyond this (no plugin system, no
config-driven source registry) — the handbook is explicit that
over-engineering against an unknown change is its own failure. Two adapters
and one assembly module is the minimum separation that pays off regardless
of what the change turns out to be.

## The degradation policy (this is the deliverable, not just the code)

Every source call collapses to exactly one of four states, always reported
explicitly to the caller — never inferred, never silently absent:

| Status | Meaning | What the caller sees |
|---|---|---|
| `ok` | Call succeeded first try | The data, plus `status: "ok"` |
| `degraded` | Call succeeded after 1+ internal retries | The data, plus `status: "degraded"` and a reason (`"succeeded after N attempts"`) |
| `unavailable` | All retries exhausted, no data obtainable | No data for that source, `status: "unavailable"`, and a reason (e.g. `http_500`, `unreachable: ...`) |
| `not_found` | Source reachable, record doesn't exist there | `status: "not_found"` — deliberately distinct from `unavailable`, because "we don't have this person" and "we couldn't reach the system" are different facts a caseworker needs to tell apart |

Concretely, for `/unified/<identifier>`:
- If the source the identifier belongs to is fully healthy → full data, `200`.
- If it needed retries → full data anyway, `200`, `status: "degraded"` on that source block so the caller knows to treat it as slightly stale/uncertain if they care to.
- If it's down after retries → `200` (the *endpoint call itself* succeeded — it did its job and reported honestly), an explicit `unavailable` block with a reason, and a top-level `error` field naming what's missing. We chose `200` over `502` here deliberately: the alternative source of truth (the caseworker's browser tabs) doesn't get an HTTP status code either, it gets a blank tab. Our job is to be clearer than that, not to imitate its failure mode. `/residents` and `/benefits` (the bulk listing endpoints) *do* return `502` on total failure, since those aren't the "graceful" combined view — they're direct pass-throughs, and a 502 there is the honest signal for "the thing you asked for, unfiltered, isn't available."
- The other source (the one the identifier *doesn't* belong to) is always reported too, as `not_queried`, with a reason — never just omitted from the response. A missing key would look like a bug; an explicit `not_queried` block does not.

This table is the answer to the problem document's specific instruction:
*"for each way a source can fail, what does the caller get, and how do they
know?"*

## Retry-safe and idempotent

There are no write operations anywhere in this system — everything is a
read. That makes "idempotent" close to automatic (a GET is naturally
idempotent), but "retry-safe" still had to be built deliberately:

- **Benefits Register (flaky, slow):** up to 3 attempts per logical call,
  small linear backoff (0.3s, 0.6s) between them. A 500, a connection
  error, and malformed XML are all treated as retryable — all three are
  documented as routine for this source. Retrying blindly forever was
  rejected: this is a real system under real load in the scenario, and an
  unbounded retry loop from a caller that's supposed to be well-behaved is
  how you turn "occasionally slow" into "reliably down for everyone."
- **Resident Index pagination:** de-duplicated by `id` into a dict as pages
  arrive, so paging twice, or a page being re-served, can never produce two
  copies of the same resident in the output. This directly targets the
  documented bug: the index is sorted by a field other processes update
  live, so the page boundary can slip and repeat a record.
- **Repeating the same request:** calling `/unified/<id>` (or any endpoint)
  twice in a row produces the same result both times (modulo the
  degraded/unavailable state of a flaky upstream at that moment, which is
  the honest answer, not a bug) — nothing is accumulated, cached-and-stale,
  or duplicated between calls.

## The pagination bug specifically

Verified with a unit test (`tests/test_dedup.py`) against a synthetic
paginator that reproduces the exact documented behaviour (last record of
page N re-served as the first record of page N+1). Also verified live: the
real service serves 620 records across pages with boundary duplication, and
`/residents` returns exactly 620 unique `source_id`s.

## No cross-source identity matching — and why we're not tempted

The problem document is explicit that this is a stretch goal, "genuinely
hard," and "easy to lose a day in" — and separately warns that being wrong
*quietly* about a match is worse than not matching at all. We took that at
face value. `/unified/<identifier>` requires the caller to supply an
identifier that already belongs to one source; it does not attempt to guess
whether some other source's record describes the same person. Where a
caseworker already knows both identifiers for someone (which the problem
statement's own scenario implies is common — that's literally what the
browser-tab-copying process is doing today), `/unified?resident_id=..&benefits_ref=..`
lets them pull both in one call. This is not identity resolution — the
correlation is supplied by the human, not inferred by us — but it removes
the same manual work the problem describes, without any risk of a wrong
silent merge.

One more thing worth recording: the raw data files (`_rest_data.json`,
`_xml_data.json`) contain a `_pid` field that would trivially link every
record across both sources. Both mock services strip this field before
serving it over HTTP. We noticed it, and deliberately did not use it — the
data pack README says the solution must go through the services, and using
a field that's invisible to any real integration would defeat the entire
point of the problem. The matcher below is built on the fields actually
exposed over the wire (name + date of birth + city), never on `_pid`.

## Cross-source matching (added as a separate, explicit feature)

After the floor was met and verified, we added an opt-in matching feature —
**not** because the floor requires it (it explicitly doesn't), but as a
deliberate quality improvement, kept fully separate from the required
`/unified` endpoint so it carries zero risk to the floor behaviour above.

What it does: `GET /match/<identifier>` takes ONE identifier (from either
source), fetches that record, then searches the full listing of the OTHER
source for the closest candidate, using a plain point-based score (last
name 40, first name 30, date of birth 25, city 5 — see
`core/matching.py`). The closest candidate and its real score are ALWAYS
shown, even when the score is low — `match_found` only turns `true` once
the score clears 70/100, but a low score is more informative than
silence, so it's never hidden. (First version of this endpoint hid
low-scoring candidates entirely; we changed that after finding a real
case — same last name and city, different first name and birthdate,
scoring 45 — where seeing "closest was 45/100, matched on last_name+city
only" is a meaningfully better answer than an unexplained "no match".)
The score and exactly which fields matched are always shown, so a human
reviewing the result can judge the risk
themselves rather than trusting an unexplained "yes."

Why this is safe to add now, this late: it lives entirely in two new
functions (`core/matching.py`, `build_matched_view` in `core/unified.py`)
plus one new route in `app/main.py`. `build_unified_view` and
`build_unified_pair` — the functions backing every endpoint used during
floor verification — are byte-for-byte unchanged. Verified by re-running
the full existing test suite (all 12 original tests still pass) plus
manual re-checks of `/unified/<id>`, `/unified?...`, `/residents`, and
`/benefits` after this change, before adding anything new.

Honest limitation: this is heuristic matching on public-facing fields, not
guaranteed correctness. Two different people could in principle share a
name and date of birth. We chose a 70-point threshold and full transparency
about matched fields specifically so this risk is visible to whoever reads
the result, rather than hidden behind a confident-looking merge.

## What we cut for time

- **Caching.** Listed as "if you have time" only. Not built. The
  degradation/retry policy above already absorbs the Benefits Register's
  slowness without it; caching would mainly help under concurrent load,
  which this problem doesn't ask us to simulate.
- **Circuit breaking.** Same — "if you have time." With only two sources
  and a bounded 3-attempt retry per call, the cost of not having one is a
  slightly slower response during an outage, not a cascading failure.
- **Identity matching.** Explicitly out of scope per the floor. See above.

## What we would fix first, given another day

- Add a lightweight in-memory cache for `/residents` and `/benefits` (full
  listings only, short TTL) — the Benefits Register in particular is slow
  enough that repeated full-listing calls are the first place a real
  caseworker would feel it.
- Build the stretch-goal matcher as an explicitly separate, off-by-default
  module (`core/matching.py`, not wired into `/unified` by default) so it
  can be demonstrated without risking the floor behaviour above it.
