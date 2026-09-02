### Changed

- **Internal (ADR-061 Phase 2 item 5, fully closed): the scoped-gate
  (`--used-by`/`--required-symbol(s)`) JSON fold is now native, not a
  post-render mutation.** `reporter.to_json`/`to_stat_json`/`_to_json_leaf`/
  `_to_json_root_cause` now accept `contract_evaluation` (plumbed from the
  CLI through `service_render.render_output` → `_render_json_output`,
  which previously dropped it on the JSON path even though the markdown
  path already received it) alongside the `severity_config`/`show_only`
  they already threaded. The new `abicheck/report/scoped_gate.py` module's
  `apply_scoped_gate` folds `--used-by`/`--required-symbol(s)` scoping
  into the in-progress JSON `dict` from inside `reporter_contract_blocks.
  render_json_with_side_facts`, right before the single `render_json`
  call — replacing `cli_compare_fold._ScopedFold.into_json`'s
  render → `json.loads` → patch → `json.dumps` round trip outright.
  `_fold_scoped_compat_into_text`'s JSON branch is now a no-op passthrough,
  and `_ScopedFold.into_oneline` calls `to_stat_json` directly instead of
  re-parsing its own `into_json` output. Every JSON field this fold
  produces (`full_verdict`, `full_severity`, `full_run_outcome`,
  `full_summary`, the scoped-only/missing-contract synthetic `changes[]`
  entries, `root_causes` regrouping) is unchanged — verified against the
  full pre-existing scoped-gate CLI-integration suite
  (`tests/test_cov95_cli.py::TestUsedByScoping`/
  `TestUsedByScopingWithSnapshotInputs`) and `test_run_outcome.py`'s
  contradiction-check suite, all passing byte-for-byte unchanged.
