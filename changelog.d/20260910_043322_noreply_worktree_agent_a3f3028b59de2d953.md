<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **`EffectiveGate`/`GateSeverityState` converge the gate-pack fold shapes**
  — single-pair `compare` (`ResolvedCompareConfig`) and the directory/
  package release fan-out (`GateOptions`) now fold a selected `kind: gate`
  pack's `gate.severity.*` contribution through one shared
  `abicheck.policy.effective_gate.GateSeverityState`, and both resolve to
  the same `EffectiveGate` shape (`exit_code_scheme`, `severity`,
  `require_complete_analysis`, `scope`) — closing the "two different
  shapes around one shared fold" gap `tests/test_release_gate_pack_fold_parity.py`
  documented (duplication-and-convergence-assessment plan's P0 target,
  scoped to the gate namespace). No CLI-visible behavior change.
- **Release fan-out scope selection is a typed `ReleaseScopePlan`** — the
  directory/package release comparison (`compare-release`) now resolves
  its ADR-065 scope-selection state (matched members, each side's
  completeness evidence) into one `abicheck.workflows.release_scope.
  ReleaseScopePlan` object, mirroring the `workflows/artifact` Request ->
  ResolvedPlan -> Result pattern, instead of threading four independent
  local variables by hand. An explicit `--select`/`--select-required`
  declared selection is now folded into the plan itself before execution
  reads `matched_keys`/`compare_keys` from it, rather than as a separate
  post-hoc filter. Purely internal; no observable behavior change.
- **`EffectiveGate` carries ADR-065's two release-fan-out gate axes** —
  `on_incomplete_scope`/`fail_on_removed_library` are now real fields on
  `EffectiveGate` (and on `GateOptions`, the release fan-out's own resolved
  object), populated from the release fan-out's already-resolved
  `--on-incomplete-scope`/`--fail-on-removed-library` values. The
  effective-config digest (`gate.on_incomplete_scope`/`gate.
  fail_on_removed_library` fields) now reads both exclusively from this
  object instead of a second, independently-derived projection of the same
  facts. `gate.fail_on_removed_library` is a new digest field (additive —
  changes the digest hash for future runs, same as any other field
  addition); `gate.on_incomplete_scope`'s value is unchanged, only its
  source moved. A bare single-pair `compare` still reports both as
  `""`/not-applicable, matching its existing behavior. No other
  CLI-visible behavior change.

