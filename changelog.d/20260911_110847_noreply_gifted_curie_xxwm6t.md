### Changed

- **No behavior change**: split several responsibility-scoped helpers out of
  `abicheck/buildsource/check_report.py` (into a new sibling
  `check_report_no_baseline.py`) and `abicheck/workflows/aggregate/load.py`/
  `fold.py` (into new siblings `no_baseline_load.py`/
  `profile_matrix_render.py`, plus moving `_LoadedReport` to `contracts.py`
  and `_analysis_assurance_exit` to `gate.py`) — pure code moves bringing
  each file back under its `architecture/debt.yaml` `no_growth` line-count
  ceiling after this session's `compare --no-baseline` audit fan-in fixes.
  Matching test files split the same way
  (`tests/test_check_report_no_baseline.py`,
  `tests/test_action_validate_inputs_audit_only.py`).
