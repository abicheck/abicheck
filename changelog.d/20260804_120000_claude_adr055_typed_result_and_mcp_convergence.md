### Added

- **`CompareResult`, a typed result for the `compare` service verb (ADR-055
  D2).** `abicheck.service.run_compare_request_v2(request)` returns a
  `CompareResult` (`diff`/`old_snapshot`/`new_snapshot`, plus the resolved
  `suppression` list) instead of the bare 3-tuple, so a future field has
  somewhere to land without breaking positional callers. The existing
  `run_compare_request` keeps its exact tuple signature and is now a view over
  the same implementation — no caller changes.
- **`InputSpec.follow_linker_scripts` (ADR-055 D4).** Lets a request decline
  to follow a GNU ld linker script's `INPUT()`/`GROUP()` target to the real
  library. Defaults to `True`, matching the previous behaviour of every
  caller; the MCP server sets it `False`, which is what keeps its
  `MCP_MAX_FILE_SIZE` guard authoritative now that it resolves through the
  shared service layer.

### Changed

- **The MCP `abi_compare` tool now routes through the Tier-2 chokepoint
  (ADR-055 D4).** It previously resolved its own inputs, loaded its own
  policy/suppression files, and used `compare_snapshots` for the middle
  diffing step only — a second compare engine that could drift from the CLI's
  without anything failing. It now builds one `CompareRequest` and calls
  `run_compare_request_v2`. Output is unchanged: a new parity test asserts
  `abi_compare`'s rendered report and exit code match the CLI `compare`
  command's across policy-profile, policy-file, suppression, `--show-only`,
  `--report-mode`, and severity-aware runs.
- **`abi_compare` rejects an unsupported `language` instead of passing it
  down.** A value outside `c`/`c++` (e.g. `"rust"`) is now a structured
  validation error, matching what the CLI's `--lang` choice has always done.

### Fixed

- **Two documentation pages quoted long-superseded schema versions.**
  `docs/use/output-formats.md` showed `report_schema_version` `"1.0"` and
  `docs/reference/check-target.md` `"2.13"`, against a real `2.26`. The
  `doc-count-sync` AI-readiness check now reads these values from
  `abicheck.schemas.current()` and fails on drift, so the next bump cannot
  leave them behind silently (ADR-055 D3).
