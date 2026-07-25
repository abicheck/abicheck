<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **The `abi_compare` MCP tool now surfaces the ADR-050 D2 comparability
  gate as a dedicated response status instead of a generic error.** A
  genuine `ProfileMismatchError`/`ScopeMismatchError` from `old_input`/
  `new_input` now returns `{"status": "not_comparable", "reason": ...}` —
  distinct from `{"status": "error", ...}`, since this is an expected,
  informative outcome, not an abicheck bug. A new `diagnostic_comparison`
  parameter (mirroring native `compare`'s `--diagnostic-comparison`)
  downgrades that into a tentative diff stamped `"assurance": "none"` in the
  rendered report.
