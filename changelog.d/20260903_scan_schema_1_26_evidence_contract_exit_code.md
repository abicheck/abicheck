### Changed

- **`scan_schema_version` bumped to `1.26`.** The evidence-contract-error
  abort's (ADR-037 D5) process exit code moved from the generic
  `ClickException` code `1` to a dedicated `_EXIT_EVIDENCE_CONTRACT_ERROR =
  7`, so the persisted JSON report's top-level `exit_code` and
  `diff.exit`/`report.exit`'s `evidence_contract_error_contribution` change
  value (both were `1`, both are now `7`) for this one abort axis. The
  `verdict` string (`EVIDENCE_CONTRACT_ERROR`) and `reasons`
  (`["evidence_contract_error"]`) are unchanged — a consumer that branches
  on those, rather than hard-coding the old numeric exit code, is
  unaffected.
