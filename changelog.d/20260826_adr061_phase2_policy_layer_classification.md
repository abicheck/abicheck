### Changed

- Closed ADR-061 Phase 2's `compare -> policy` dependency blocker, so
  `severity.py` and `analysis_assurance.py` are now classified into the
  `policy` responsibility layer in `architecture/modules.yaml` with
  `check_architecture.py` reporting zero findings. Re-measuring the recorded
  blocker found one real forbidden edge rather than the five it named —
  `checker_types.py`'s function-local import of
  `severity.effective_verdict_for_change`, which also closed the
  `compare -> policy -> compare` cycle against `analysis_assurance.py`'s own
  (allowed) import of `DiffResult`. That edge is removed by moving the shared
  logic to a leaf both layers may depend on: `effective_verdict_for_change`,
  `reclassify_rule_for_change`, and the `KindSets`/`resolve_kind_sets` pair
  they share now live in `reclassify.py`, which already owned the
  selector-scoped rules the resolver's precedence chain is built around.
  Behavior is unchanged and no public name moved — `severity.py` re-exports
  all three, and `DiffResult`'s `breaking`/`source_breaks`/`compatible`/
  `risk` properties are untouched.
