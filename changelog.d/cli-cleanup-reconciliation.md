### Changed

- **Cleanup after `scan` was retired (ADR-068).** `policy.exit_decision.
  resolve_exit_decision` no longer accepts `crosscheck_promotion_contribution`.
  Its only producer was the deleted `scan --crosscheck KEY=error`, so every
  current decision already reported it as `0`. `ExitDecision.
  crosscheck_promotion_contribution` and `ExitReason.PROMOTED_CROSSCHECK`
  stay read-only, so the report `exit` block keeps its schema and a stored
  pre-0.6 `scan` report still round-trips. Comments and docstrings across
  `abicheck/` that described `scan_engine.py`/`cli_scan*.py` as live code now
  mark them as retired. No runtime behavior changes.
