<!-- Codex review follow-up round 4 on CLI cleanup phase two, PR 2. -->

### Fixed

- **A manifest/run-plan `gate` block (or one of its sub-keys) explicitly set
  to JSON `null` is now rejected instead of silently treated the same as
  the key being absent.** `"gate": null`, `"gate": {"missing_required":
  null}` and similar previously fell back to the hard-coded `fail`/`include`
  defaults with no error — the exact same silent-misapplication failure
  mode the `2.0`/`v2` version bumps were written to close, reached through
  an explicit-null value instead of a stale version.
- **`abicheck.schemas.current("run-plan")` now reports the gate-bearing
  `abicheck.run-plan/v2` schema, not the base `v1`.** `run-plan.json` is
  stamped one of two schema strings depending on whether it carries a
  `gate` block, so there is no single fixed version this artifact always
  emits — the registry now reports the highest version abicheck can
  produce, which is what an external integrator needs to be prepared to
  parse.
- **`aggregate`'s `effective_policy.source` now reports `"explicit"`,
  not `"default"`, when a direct Python-API caller forces
  `on_missing_required`/`on_unexpected_target`.** The resolved value in
  that case is the caller's own override, not the hard-coded default
  (`fail`/`include`), so labeling it `"default"` misrepresented the audit
  field. `resolve_gate_policy()`'s precedence (explicit > manifest/run-plan
  > default) is unchanged; only the reported source label for the explicit
  case is corrected.
