### Changed

- **`compare --surface-metrics`/`--show-filtered`/`--audit-suppressions`
  computation is unconditional now** (ADR-068 D4/Phase 5): every comparison
  always computes the ADR-027 public-surface metric-drift findings
  (`public_surface_grew`/`public_surface_shrank`/
  `undocumented_export_ratio_increased`), the ADR-067 disposition/
  out-of-surface ledger, and the suppression audit (when `--suppress` is
  given) — the three flags now only ever gate *rendering* of an
  already-computed section, matching the precedent `--audit-suppressions`
  already established. `--audit-suppressions` with no `--suppress` is a
  no-op now (nothing to audit) instead of a usage error.
- **`compare --view` replaces `--report-mode`/`--show-only`/`--demangle`/
  `--no-demangle`/`--explain-patterns`** (ADR-068 D4/Phase 5): one
  repeatable, purely presentational option collapsing all four concepts —
  `--view full|leaf|impact|root-cause` (report mode), `--view show=<tokens>`
  (the former `--show-only` filter, repeatable to OR further groups
  together), `--view demangle|no-demangle`, and `--view patterns` (the
  former `--explain-patterns`, now explaining the always-on pattern-verdict
  modulation rather than ever enabling it). The four retired flags exit `64`
  with no deprecated alias.

### Removed

- **`compare --report-mode`, `--show-only`, `--demangle`/`--no-demangle`,
  and `--explain-patterns`** — replaced by the single repeatable `--view`
  option (see the `Changed` entry above). `--surface-metrics` itself is
  *not* removed — it is accepted for backward compatibility but is now a
  no-op, since its computation always runs.
