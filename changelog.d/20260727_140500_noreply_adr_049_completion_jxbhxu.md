<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **ADR-049 Phase 3 shadow evaluator: robustness and maintainability fixes**
  (`checker.py`, `contract_evaluation.py`; opt-in via
  `compare(..., contract_evaluation=True)`; no default-path behavior
  change): `_apply_contract_evaluation_shadow`'s `zip(all_changes,
  decisions)` now passes `strict=True` (CodeRabbit review; Ruff B905) so a
  future evaluator that returns a mismatched number of decisions raises
  instead of silently leaving some findings unstamped. The unreachable
  `assert reason in _ALL_SURFACE_REASONS` guard in
  `evaluate_change_contract_relevance` is removed (CodeRabbit review): the
  function's own docstring promises it never raises for a finding it
  cannot confidently classify, and `_decision_for_surface_reason` already
  degrades gracefully for an unrecognized reason, so the assert only risked
  turning a future `surface.py` addition into a crash instead of the
  documented degradation. `_NOT_APPLICABLE_KIND_SLUGS` is now derived from
  real `ChangeKind` members (`_NOT_APPLICABLE_KINDS`) instead of raw string
  literals, matching the existing `_PUBLIC_SOURCE_ABI_KIND_SLUGS` pattern,
  so a misspelled/stale entry fails loudly at import time instead of
  silently degrading to "not in the set" (CodeRabbit review). Replaced an
  ambiguous `∪` character in a docstring with plain wording (Ruff RUF002).
