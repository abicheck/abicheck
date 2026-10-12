### Changed

- **CLI runtime keeps only terminal concerns** — snapshot provenance stamping
  moved from `frontends/cli/runtime.py` to the new
  `abicheck.workflows.provenance` (`stamp_provenance`, `provenance_timestamp`,
  the latter formerly `cli_helpers_compare._provenance_timestamp`), and the
  operand-metadata / same-binary-note step now has one owner,
  `workflows.compare_finalize.attach_operand_identity`, which both the native
  `compare` CLI and the typed API's `finalize_classified_pair` call instead of
  keeping separate copies. The `--view` Click callback moved to
  `frontends.cli.options.view.validate_view`, the CLI's `_render_output`
  wrapper to its only caller `frontends/cli/compare_report.py` (now reaching
  `frontends.render` rather than the `service` facade), and the
  delegation-only `_resolve_debug_artifact`/`_collect_metadata` wrappers were
  deleted. No behavior change.
