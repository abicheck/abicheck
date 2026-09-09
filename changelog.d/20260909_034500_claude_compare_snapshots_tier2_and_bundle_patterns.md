<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- `abicheck.service.compare_snapshots()` — the documented public Tier-2
  comparison verb — still trusted its own `pattern_verdicts`/
  `surface_metrics` parameters, which default to `False`. A direct typed
  API caller (bypassing the CLI and the release/bundle drivers that
  already forced both `True` at their own call sites) could still get
  pattern-verdict modulation and surface-metrics findings disabled for an
  otherwise-identical comparison. Both are now forced unconditionally
  inside `compare_snapshots()` itself, matching the existing
  `cross_source_checks` "no legitimate off position" precedent; the two
  parameters remain accepted for compatibility but are now ignored.
- `compare --view patterns` against a stored-BundleFacts `OLD_INPUT` is
  now rejected explicitly (exit 64) instead of silently doing nothing —
  that comparison shape has no stderr-echo channel for the pattern-
  modulation ledger, the same class of gap `--view demangle`/
  `--no-demangle` already reject for the identical input shape. The
  ledger itself is unaffected and still appears in that comparison's own
  JSON output unconditionally.
