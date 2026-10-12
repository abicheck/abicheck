### Changed

- **CLI runtime keeps only terminal concerns** — snapshot provenance stamping
  moved from `frontends/cli/runtime.py` to the new
  `abicheck.workflows.provenance` (`stamp_provenance`, `provenance_timestamp`,
  the latter formerly `cli_helpers_compare._provenance_timestamp`), so any
  front end that writes a snapshot reaches it. The `--view` Click callback
  moved to `frontends.cli.options.view.validate_view`, the CLI's
  `_render_output` wrapper to its only caller `frontends/cli/compare_report.py`
  (now reaching `frontends.render` rather than the `service` facade), and the
  delegation-only `_resolve_debug_artifact`/`_collect_metadata` wrappers were
  deleted (`workflows.extraction.resolve_debug_info` and
  `workflows.input_resolution.collect_metadata` are the owners). No behavior
  change.
