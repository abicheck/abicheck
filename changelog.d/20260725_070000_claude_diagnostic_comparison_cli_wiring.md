<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **The native `abicheck compare` command now enforces ADR-050's
  comparability gate instead of crashing on it.** Previously, comparing two
  snapshots extracted under an incomparable profile/scope contract (a
  `ProfileMismatchError`/`ScopeMismatchError` from `abicheck.comparability`)
  propagated out of `compare_snapshots` as an unhandled Python traceback —
  the checker-level gate (`checker.compare`) and `dumper.py`'s real
  `ExtractionContract` attachment already existed, but the CLI's own
  `compare_snapshots(...)` call site had no exception handling and no way
  to opt into the documented escape hatch. `compare` now exits `16` with a
  clear message on a genuine mismatch (`--format json` additionally emits a
  schema-conformant `{"verdict": null, "reason": {"kind": ..., "message":
  ...}}` document, `compare_report.schema.json`/`REPORT_SCHEMA_VERSION`
  bumped to 2.17), and a new `--diagnostic-comparison` flag downgrades that
  hard failure into a tentative diff (stamped `assurance: "none"`) for
  callers who understand the risk. `--format json` output also gains the
  `contract_coverage`/`assurance` fields on an ordinary completed
  comparison, when applicable. Still open: the other five ADR-050 D2 entry
  points (`mcp_server.py`, `cli_compare_release.py`'s release fan-out,
  `compat/cli.py`, `cli_scan.py`'s `scan --against`, `stack_checker.py`'s
  `deps compare`) don't have their own exception handling yet — see
  `docs/development/plans/g32-comparability-contract-and-multi-tu-manifest.md`
  Phase A.
