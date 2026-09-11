<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Test import path fixed after `cli_compare_helpers` -> `compare_report`
  split** — `tests/test_cli_contract.py::
  test_contract_evaluation_no_longer_rejected_for_directory_comparisons`
  imported `_reject_set_input_flags` from `abicheck.cli_compare_helpers`,
  which stopped re-exporting it once an earlier refactor moved the code
  that used it into `abicheck/frontends/cli/compare_report.py` without
  carrying that one import along (unlike the four helpers that *were*
  physically moved and explicitly re-exported back). The function was
  never defined in `cli_compare_helpers` in the first place — it lives in
  `abicheck.cli_compare_options`, which the test now imports from
  directly, matching every other call site in the codebase.
