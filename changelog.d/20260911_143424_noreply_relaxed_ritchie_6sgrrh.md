### Removed

- The `abicheck scan` root command and every module exclusive to it
  (`cli_scan*.py`, `service_scan.py`, `scan_engine.py`, `pr_comment_scan*.py`,
  `scan_abi3_resolve.py`, `frontends/cli/scan_*.py`,
  `frontends/cli/artifact_set_dry_run.py`, and `workflows/scan_*.py`) have been
  removed, along with the typed `ScanRequest`/`ScanResult`/`ScanSetResult`
  Python API and `--artifact-set`. A one-build audit is now `dump`; comparing a
  build against a stored baseline is `compare`. The shared header-input
  expansion helpers `service_scan.py` also held moved to the leaf module
  `abicheck/workflows/header_inputs.py`, and `abicheck.service` still
  re-exports `expand_header_inputs`, `CompileContext` and
  `pair_wide_cxx20_std_override` unchanged.
