# Changelog

## v0.1.1 — 2026-10-13
- Round-2 fixes found by self-audit (3 failing cases → green):
  - ledger files are now read with `utf-8-sig`, so BOM-prefixed ledgers verify cleanly instead of failing JSON parse on line 1.
  - `verify` on a malformed (unparseable) ledger now exits 2 (input failure) instead of a confusing findings exit — consistent with `check`/`report`/`add`.
  - drift on negative baselines documented and tested: denominator is `|old|`, so a loss moving -100 → -80 reports +20.0% (magnitude of change), not a double negative.
- Self-test re-run: `python self_test.py` → `SUMMARY: PASS 65/65` (was 62/65 at first draft).

## v0.1.0 — 2026-10-13
- Initial implementation: hash-chained JSONL ledger (sha256 over prev hash + canonical JSON), four subcommands — `add` (schema validation, ISO ts, append-only ts ordering, env k=v annotation), `check` (last-two drift with `--threshold`, env-diff attribution, unexplained-drift reporting, baseline-zero guard), `verify` (full chain recompute), `report` (per-series summary). CLI exit 0/1/2 contract.
- Self-test suite (functional / error / extreme-edge tiers), 65 cases at first draft.
