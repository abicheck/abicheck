<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A leak on a dependency record that a release starts naming publicly gates again** —
  an `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` finding on a header-only dependency's
  record was moved out of contract (`dependency_internal_unreachable`) when the
  record was unreachable on *either* side. A public declaration that starts (or
  stops) naming the record, so it is internal on one side and reached on the
  other, therefore hid a real layout change. The leak now follows the record's
  presence: a record only one side declares is judged on that side, and a
  record both sides declare must be internal on both. The intel/xvec field
  report's leaks, on records each declared by one build only, stay out of
  contract (ADR-089, 2026-10-26 amendment).
