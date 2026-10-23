### Changed

- **Compare CLI helpers split by owner** — the Click-facing post-comparison
  reporter (`_report_compare_result`, `_attach_use_case_impact`) moved to
  `frontends/cli/compare_result.py`, and the manifest preflight and
  evaluation-config adapter (`_preflight_manifests_and_audit`,
  `_resolve_evaluation_config`) to `frontends/cli/compare_preflight.py`.
  `cli_compare_helpers.py` shrinks from 1561 to 1216 lines; no CLI output,
  exit code or public API changes.

