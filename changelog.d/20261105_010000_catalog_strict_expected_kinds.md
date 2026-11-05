### Changed

- **A catalog lane cell now FAILs when an expected ChangeKind is missing, not
  only when the verdict differs.** In the native `validate_examples.py` lanes
  (`debug-headers`, `build-source`) a missing `expected_kinds` entry or a
  present `expected_absent_kinds` entry fails the row, per header frontend
  (`ABICHECK_AST_FRONTEND`) and mode on Linux x86_64 (other targets keep it
  reported-only), unless `catalog/ground_truth.json`
  declares that gap under the new `expected_kinds_gaps` field, which names
  the kinds, the modes, a reason, and the `docs/contribute/known-gaps.md`
  entry that tracks it. A declaration the lane no longer reproduces fails as
  stale. The unscoped `known_kind_gap`/`known_kind_gap_note` fields are
  retired and the `ABICHECK_STRICT_KINDS` switch is gone; the verdict-level
  `known_gap`/XFAIL rules are unchanged (`tests/expected_kinds_gate.py`).
  The clang header frontend now runs the whole catalog in the PR clang leg
  and both native lanes nightly. New open gap: a `--build-info` dump loses
  `public_api_internal_dependency_added` (case187/188/189/191).
