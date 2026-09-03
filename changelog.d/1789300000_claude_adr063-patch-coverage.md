### Changed

- **Line coverage for ADR-063 Track 1's new modules brought to 100%.**
  `compare/opaque_types.py`, `compare/typedefs.py`,
  `model/identity_tiers.py`, and `model/semantic_ir_legacy_adapter.py` had
  a handful of uncovered branches — the by-value-exposure scan's
  already-found short-circuits, and two private typedef-cutover helpers
  (`_has_version_family_successor`, `_aliases`) whose branches no real
  caller happened to exercise, since a real call site only reaches them
  after the condition they check has already been established elsewhere.
  Added direct tests for each (Codecov patch-coverage gate on PR #1041).
