<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A genuine constant/default edit between two equally-legacy baselines
  could be silently dropped** (`diff_default_value_reliability.py`): both
  `default_value_fingerprint_comparison_unreliable` (`Param.default`/
  `TypeField.default`) and `constant_value_fingerprint_comparison_unreliable`
  (constants) suppressed a comparison whenever *either* side was
  independently marked unreliable (an OR) — but two archived snapshots that
  both came from the same pre-schema-v20 direct-clang build use the
  *identical* legacy fingerprint algorithm and are directly comparable to
  each other. Only a *mismatch* between the two sides' extraction
  generations (one legacy, one stabilized) is actually unsafe to compare.
  Both guards now delegate the suppress/don't-suppress decision to a single
  shared `_fingerprint_comparison_unreliable`.

- **A follow-on to the fix above had its own mirror bug**: naively
  collapsing "is this side a fingerprint AND is it legacy" into one boolean
  before comparing lost the value-SHAPE distinction — a legacy fingerprint
  on one side compared against a plain literal on the other read as
  "generations differ" and wrongly suppressed a confirmed
  expression-to-literal (or literal-to-expression) edit, a change no
  algorithm version could ever produce spuriously (the unstable algorithm
  only affects a compound expression's fingerprint *hash*, never whether a
  declaration's own value was a literal at all). `_fingerprint_comparison_
  unreliable` now only ever suppresses when BOTH sides are genuinely
  fingerprint-shaped, and even then only on a legacy-generation mismatch.

### Added

- **Exhaustive, invariant-based test coverage for the fingerprint-
  reliability guards and the degraded-facts warning's producer/header-
  confirmation gate table.** PR #720's review found seven distinct bugs
  across these two small modules over seven rounds — each one a different
  cell of the same small, finite (producer × reliability/confirmation ×
  value-shape) state space, none caught beforehand because every existing
  test picked one hand-crafted scenario per property instead of covering
  that space. `tests/test_fingerprint_reliability_properties.py` (new) and
  `tests/test_schema_compat.py::test_degraded_facts_warning_grid` (new)
  enumerate their respective state spaces completely rather than sample
  them, so a future regression in either area fails immediately instead of
  waiting for a real customer's corpus to surface it. See
  `diff_default_value_reliability.py`'s module docstring for the full
  retrospective.
