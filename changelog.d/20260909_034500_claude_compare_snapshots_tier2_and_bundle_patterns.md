<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- `abicheck.service.compare_snapshots()` — the documented public Tier-2
  comparison verb — still trusted its own `surface_metrics` parameter,
  which defaults to `False`. A direct typed API caller (bypassing the CLI
  and the release/bundle drivers that already forced it `True` at their
  own call sites) could still get surface-metrics findings disabled for an
  otherwise-identical comparison. `surface_metrics` is now forced
  unconditionally inside `compare_snapshots()` itself (ADR-027 Phase 5);
  the parameter remains accepted for compatibility but is now ignored.
  (An accompanying change to force `pattern_verdicts` the same way was
  reverted in a follow-up fix in this same PR — see below — since ADR-027
  explicitly defers that flip and it silently broke `scan --against`'s own
  working opt-in flag.)
- `compare --view patterns` against a stored-BundleFacts `OLD_INPUT` is
  now rejected explicitly (exit 64) instead of silently doing nothing —
  that comparison shape has no stderr-echo channel for the pattern-
  modulation ledger, the same class of gap `--view demangle`/
  `--no-demangle` already reject for the identical input shape. The
  ledger itself is unaffected and still appears in that comparison's own
  JSON output unconditionally.
