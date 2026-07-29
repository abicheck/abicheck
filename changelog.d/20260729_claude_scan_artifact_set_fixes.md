<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --artifact-set` (ADR-056/G35): three Codex-review bugs in the new
  multi-artifact scan path fixed before merge.** (1) `_SCAN_SET_COMPAT_ORDER`
  tied `NO_CHANGE` and `COMPATIBLE` at the same rank; the aggregation loop's
  `>=` tie-break means the *last* candidate at a tied rank wins, and the
  bundle audit's own verdict — always appended last, and often `NO_CHANGE`
  when it simply found nothing to flag — could silently override a real,
  positive `COMPATIBLE` result from every member scan. `NO_CHANGE` now ranks
  strictly below `COMPATIBLE`, so a genuine "I compared this and it's fine"
  always outranks "there was nothing to compare here," while an all-`NO_CHANGE`
  set still resolves to `NO_CHANGE`. (2) The "these flags only mean anything
  with `--against`" guard (`--suppress`, `--policy-file`, `--policy`,
  `--scope-public-headers`, `--strict-suppressions`, `--public-symbol`,
  `--public-symbols-list`, `--pattern-verdicts`, `--env-matrix`) only ran on
  the single-binary path; `--artifact-set` (always audit-only, never has
  `--against`) bypassed it entirely via an early return, so these flags were
  silently parsed and discarded instead of erroring. Extracted into a shared
  `_reject_comparison_only_flags` helper now called from both paths. (3)
  `run_scan_set()` loads `--risk-rules` via the click-free
  `_load_risk_rules_for_service()`, which converts `click.ClickException`
  into a plain `ValueError` (since `service_scan.py` must also be reachable
  from the MCP server/Python API); `_run_artifact_set()`'s own `try`/`except`
  only caught `ArtifactSetError`, so a malformed/unreadable `--risk-rules`
  file surfaced as an unhandled Python traceback and exit 1 instead of a
  clean usage error. Now also catches `ValueError` and re-raises as
  `click.UsageError`.
- **`abicheck/service.py` trimmed back under the 2000-line AI-readiness hard
  cap** via comment compaction on two adjacent re-export blocks, after an
  independent main-branch commit landed the file exactly at the cap and this
  branch's own `ScanArtifactResult`/`ScanSetResult`/`run_scan_set`/
  `run_scan_set_subprocess` re-exports pushed it 8 lines over.
