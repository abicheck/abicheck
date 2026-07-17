<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **P0: `scan --depth headers` no longer ignores `--budget` while parsing a
  pathological header, and no longer orphans clang/castxml child processes on
  timeout.** Real-world field report (Intel SVS): a 3-header scan with heavy
  template/include complexity ran over 15,000s and 3+ GiB RSS before an
  *external* `SIGKILL`, because (1) `--budget` was only checked once, after
  the whole scan had already finished, and (2) the internal clang/castxml
  `subprocess.run(..., timeout=120)` call had no process-group isolation, so
  on a timeout only the direct child was killed — a compiler-driver
  grandchild could survive as an orphan indefinitely. New `abicheck.deadline`
  module threads a shrinking scan-wide deadline down to the L2 header-parse
  subprocess boundary (checked *before* each clang/castxml invocation, not
  only at the end) and runs that subprocess in its own process group so a
  timeout kills the whole tree via SIGTERM→SIGKILL escalation, unconditionally
  (not only when the direct child's own exit is what times out — a grandchild
  that traps/ignores SIGTERM would otherwise dodge it). The deadline also
  covers `--against` baseline comparisons, not just the candidate snapshot.
  An in-flight timeout under an active budget is reported as the dedicated
  budget-overflow exit code, distinct from an ordinary parse timeout. A
  user-set `--budget` is honored up to its full value rather than silently
  re-capped to the old fixed 120s. `scan --dry-run`'s L2 header cost estimate
  also no longer reports a falsely precise number for a small-but-pathological
  header: headers with deep `#include`/template complexity are flagged and
  priced conservatively instead. This is a **bounding** fix, not a speedup —
  a genuinely pathological header still costs whatever clang/castxml need, up
  to whatever `--budget` is given; see
  [performance.md § L2 header-scan deadline enforcement](docs/development/performance.md#l2-header-scan-deadline-enforcement-pathological-headers)
  for the perf-tracking coverage.
- **The same deadline/process-group fix extended to every other clang/castxml
  subprocess call site in the scan pipeline.** `abicheck.deadline.run_bounded`
  now backs `preprocessor_scan.py`'s macro/include-leak pre-scan (degrades a
  per-TU/per-header timeout to a diagnostic + skip rather than aborting the
  advisory pre-scan, and stops iterating the rest of the compile DB once the
  budget is gone) and the L4 source-replay extractors
  (`source_extractors/clang.py`, `source_extractors/castxml.py` — a deadline
  overflow folds into the same `SourceExtractionError`/partial-coverage
  contract an ordinary timeout already used). Also: a `--budget` deadline
  expiring while a PE/Mach-O binary's header-scoped dump was mid-parse used
  to be silently swallowed by the same broad `except Exception` that falls
  back to export-table mode for a merely-unavailable header backend — it now
  propagates so `run_scan_core` reports the budget-overflow exit code instead
  of a degraded-but-"successful" result. And the L2 clang header-AST
  subprocess itself no longer buffers its (potentially multi-GiB) stdout into
  a Python `str` — it streams to a temp file like the L4 replay already did,
  closing the memory side of the same field report (measured ~27% lower
  Python-heap peak on a real pathological-header fixture).
- **The scan deadline now propagates into L4 replay's thread/process-pool
  workers, and the MCP `run_scan_subprocess` watchdog can now reach a
  clang/castxml child that detached into its own process group.**
  `contextvars` (which carry the active `--budget` deadline) don't cross a
  `ThreadPoolExecutor`/`ProcessPoolExecutor` boundary, so `source_replay`'s
  parallel L4 extraction workers used to see no active deadline at all and
  silently fall back to each extractor's fixed default timeout regardless of
  `--budget`; `deadline.py` gained `current_deadline_ts()`/`with_deadline_ts()`
  to capture and re-establish it explicitly across that boundary. Separately,
  `run_bounded`'s own process-group isolation (needed so its *inner* timeout
  can kill a compiler subtree without risking a self-kill of the calling
  process) had an unintended side effect for the MCP scan path: a clang/
  castxml child now detaches into its *own* session, invisible to the outer
  worker-level `killpg` in `service_scan.run_scan_subprocess` if *that*
  timeout fires first — an orphaned-compiler regression of the very bug this
  fix set out to close, just one layer further out. The outer watchdog now
  walks the live process tree by PPID (`_descendant_pgids`) to find and kill
  every such detached group too, not just the worker's own.
