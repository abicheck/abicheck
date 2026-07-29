<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --artifact-set` (ADR-056/G35): three more Codex/CodeRabbit-review
  bugs fixed before merge.** (1) An ambiguous duplicate-`DT_SONAME` set was
  previously only discovered inside `audit_bundle()`, which runs *after*
  every member's own scan; if an earlier member scan then exhausted
  `--budget`, the real "ambiguous duplicate SONAME" usage error was masked
  as an ordinary `BUDGET_OVERFLOW`, and every member's own (potentially
  expensive) scan already ran for a request that was always going to be
  rejected. `bundle.check_artifact_set_soname_collisions()` now runs right
  after discovery, before any member is scanned. (2) `run_scan_set()`, once
  made public/re-exported, only ever validated `req.baseline` itself — a
  direct Python API caller passing a baseline-comparison-only field
  (`policy`, `policy_file`, `suppression`, `env_matrix`, ...) to it got the
  field silently accepted and discarded under default audit classification,
  unlike an equivalent `run_scan()` call (which already rejects the same
  fields when there's no baseline to apply them to). Both entry points now
  share one `_reject_comparison_only_fields()` validator. (3) The
  `--artifact-set` text report fell through to printing
  `"Bundle analysis: None (0 finding(s))"` when the set-level verdict was
  `BUDGET_OVERFLOW` (the bundle audit never ran, so `bundle_verdict` stays
  at its `None` default) — now renders `"Bundle analysis: not run (budget
  overflow)"` instead.

### Documentation

- Corrected ADR-023's ambiguous `"as ADR-002's amendment"` self-reference
  and removed an extra verification-state token from its `**Status:**`
  line; removed ADR-056's YAML front matter and shortened its `**Status:**`
  line to match this repo's ADR convention (plain heading + Date/Status/
  Decision maker, no front matter, no packed narrative in the Status line);
  fixed ADR-056's "Implementation plan" section and the `docs/contribute/
  plans/index.md` G35 row, both of which still said "not started"/"nothing
  started" despite the Status header itself already noting Phases 1-4
  shipped ahead of formal sign-off. Reworded three pages
  (`docs/learn/abi-api-handling.md`, `docs/learn/abi-series/
  01-foundations.md`, `docs/reference/tool-comparison.md`) that hand-copied
  the exact "396 change kinds" count to instead link to the Change Kind
  Reference, which already owns that fact.
