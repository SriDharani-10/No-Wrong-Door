# AI usage

Used Claude (Anthropic) throughout, via chat, for:

- Scaffolding the overall structure (adapters / core / app split) after we
  decided on the architecture described in DECISIONS.md.
- Writing the first draft of both adapters (`adapters/resident_index.py`,
  `adapters/benefits_register.py`) and the assembly layer
  (`core/unified.py`), from a spec we gave it of the desired behaviour
  (retry counts, backoff, status taxonomy).
- Writing the stdlib HTTP server (`app/main.py`) and the unit tests
  (`tests/`).
- Drafting this file and the initial pass of `README.md` / `DECISIONS.md`,
  which we then edited for accuracy against what was actually built and
  tested.
- Debugging: diagnosing why background service processes were dying
  between shell commands during local testing (needed `setsid` to fully
  detach them), and confirming the retry/degradation behaviour by manually
  hammering the API and inspecting real responses (see the "Try it"
  section of the README and the failure-injection runs recorded in
  DECISIONS.md).

Not delegated to AI: the interpretation of the problem statement's
degradation and identity-matching requirements, the decision to keep
`/unified` at 200 even on partial failure, and the decision not to use the
`_pid` field found in the raw data files — those are our calls, made after
reading the problem document and data pack ourselves.

Every function in this repository was read and can be explained by us,
including what it does with inputs we haven't explicitly tried.
