<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **ADR-049 Phase 3: dedupe the shadow evaluator's surface-reason mapping**
  (no behavior change): `contract_evaluation.py`'s terminal-vs-weak
  surface-exclusion-reason mapping (`_TERMINAL_SURFACE_REASONS` →
  `PROVEN_OUT_OF_CONTRACT`, else → `UNKNOWN_UNRESOLVED`) was implemented
  twice -- once for the already-excluded-by-pipeline short-circuit, again
  for the fresh `classify_change_surface()` fallback. Extracted into a
  single `_decision_for_surface_reason()` helper both branches share, so a
  future change to one can't silently miss the other.
