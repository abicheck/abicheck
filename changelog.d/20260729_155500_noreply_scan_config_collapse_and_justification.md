### Fixed

- **`scan --against` now honors the project config's
  `scope.collapse_versioned_symbols` and `suppression.require_justification`**
  the same way `compare` does — `resolve_compare_config` already computed
  both values, but `scan_cmd` discarded them instead of threading them
  through `run_scan_core`/`_run_baseline_compare`/`compare_snapshots` and
  `_load_suppression_and_policy` respectively. Concretely: an ICU-style
  version-suffix rename previously reported `BREAKING` under
  `scan --against --config` while `compare --config` correctly reported
  `COMPATIBLE_WITH_RISK`, and a reason-less `--suppress` rule was silently
  accepted by `scan --against --config` even when the project config set
  `suppression.require_justification: true` (Codex review, PR #657).
