<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A clean, completed `compare --no-baseline` audit no longer reads as
  unavailable coverage.** `TargetReport.analyzed` was `compatibility_verdict
  is not None` alone, and a no-baseline audit's `compatibility_verdict` is
  always `None` by design (ADR-068 D2) — indistinguishable from a report
  that never arrived. A required, clean audit therefore reported
  `CoverageStatus.EMPTY` and failed `abicheck aggregate` at exit 1 even
  though it ran successfully. A new `TargetReport.
  completed_without_compatibility_verdict` flag, set only for this shape,
  widens `analyzed` without changing any other report kind's behavior.
- **A no-baseline audit's real candidate-side findings now reach
  multi-profile fan-in.** Its findings live in a root-level `findings`
  array, distinct from the always-empty `changes` — previously discarded
  outright, so the findings that produced `AUDIT_GATE` could not be
  reconciled or displayed, and another profile could be wrongly treated as
  unaffected.
