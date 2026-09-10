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
  local variables by hand. Purely internal; no observable behavior change.

