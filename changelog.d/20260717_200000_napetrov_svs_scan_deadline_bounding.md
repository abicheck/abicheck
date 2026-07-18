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
- **A `--budget` that expired exactly as clang finished successfully no
  longer silently lets the AST JSON load and walk run well past it.** The L2
  clang header-AST result parser only checked the deadline before spawning
  the subprocess; a pathological header's AST can be hundreds of MB to
  multiple GB, so loading and walking it costs real time on its own.
  `deadline.check()` now re-fires right after the subprocess returns, before
  that load starts.
- **A warm L2 AST cache hit now re-checks the deadline too, not just the
  subprocess path.** Reading and parsing a cached clang/castxml AST costs
  real time on its own for a large cached header set; both cache-hit
  branches (`_clang_header_dump`, `_castxml_dump`) now call
  `deadline.check()` before consuming the cache entry.
- **Three more "check the deadline before sinking real time into a large
  parse" gaps closed, same shape as the two above.** The L2 castxml path
  (`_validate_castxml_output`) now re-checks before parsing a freshly
  produced (not cached) XML tree. The L4 clang source extractor
  (`ClangSourceExtractor.extract`) had the identical AST-load gap as the L2
  clang path — now closed, folded into the existing `SourceExtractionError`
  contract (L4 failures degrade to partial coverage, never abort the scan,
  ADR-028 D3). And the castxml↔clang system-include parity probe
  (`_probe_gnu_system_includes`, up to two `gcc`/`g++ -E -v` calls per L2
  clang dump) used a fixed 15s `subprocess.run` with no process-group
  isolation and no deadline awareness at all — a tight `--budget` could lose
  up to ~30s to a slow/hung probe before the budget-aware clang invocation
  was ever reached. It now goes through `deadline.run_bounded` like every
  other subprocess call in this pipeline (gained an `input=` parameter to
  support the probe's stdin-fed invocation), degrading to its existing
  best-effort `[]` fallback on a `DeadlineExceeded` exactly like a timeout.
- **The L5 call/type-graph fold (`--depth source`'s `fold_call_graph`/
  `fold_type_graph`) now respects `--budget` too.** Both
  `call_graph.ClangCallGraphExtractor` and `type_graph.ClangTypeGraphExtractor`
  had the identical bare-`subprocess.run(timeout=120)` /
  `ThreadPoolExecutor`-with-no-deadline-propagation anti-pattern already fixed
  in `source_replay.py`'s L4 dispatch. Both now go through
  `deadline.run_bounded` (degrading to their existing diagnostic+`[]`
  best-effort contract on overflow — this pass is advisory, ADR-028 D3) and
  share a `_deadline_bound_worker` helper (`call_graph.py`) to re-establish
  the captured deadline inside each `ThreadPoolExecutor` worker, the same
  pattern `source_replay._deadline_bound_worker` uses for L4.
- Closed the last L5 gap Codex found on the same subprocess: the L5 clang
  invocation could exit successfully right as `--budget` expired, and
  `ClangCallGraphExtractor`/`ClangTypeGraphExtractor` would then still run
  `json.loads()` plus the full AST walk (`parse_clang_ast_calls`/
  `parse_clang_ast_types`) with no deadline check, mirroring the L2/L4
  post-run gap already fixed elsewhere. Both now re-check `deadline.check()`
  right after the subprocess returns and before parsing, degrading to the
  same advisory diagnostic+`[]` contract (ADR-028 D3) on overflow.
- Swept the rest of the L3/L5 build-resolution path for the same bare-
  `subprocess.run` anti-pattern (self-audit that turned up the same gap
  Codex independently flagged on `include_graph.py`): `include_graph.py`'s
  `ClangIncludeExtractor` (the `clang -M` include-map pass reached from
  `collect_inline_pack` for both L5 folding and `--header-graph`),
  `build_query.py`'s zero-config `cmake`/`bazel`/`make` query (and its GNU
  Make `--version` probe), and `inline.py`'s operator-configured trusted
  `build.query` command all ran inside the scan's active `--budget` deadline
  scope but only consulted their own fixed local timeouts (30s/10s/600s/300s
  respectively), never the `ContextVar` deadline. All three now go through
  `deadline.run_bounded`, degrading to their existing failed/diagnostic
  contract on a `DeadlineExceeded` instead of running past the budget.
- Closed the last castxml-side probe gap: `_castxml_version_note()` (the
  `castxml --version` probe on a frontend-too-old failure, folding an
  upgrade note into the diagnostic) used a bare 15s `subprocess.run`,
  reached on the castxml-*failure* path before `_validate_castxml_output`'s
  existing post-success `deadline.check()`. Now goes through
  `deadline.run_bounded`.
- Correction to the above: a `DeadlineExceeded` from that probe is **not**
  an ordinary probe failure — degrading it to `""` (as the first pass did)
  let a budget overflow during the probe masquerade as a normal
  `HeaderToolchainError`/`SnapshotError` (CLI exit 1) instead of the
  documented budget-overflow exit 5. It now propagates uncaught, like the
  castxml/clang subprocess calls around it on this authoritative L2 path
  (ADR-028 D3 draws the advisory/authoritative line at L3+, not L2).
- Correction to the `include_graph.py` fix: `deadline.run_bounded()` honors
  an active outer `--budget` deadline *verbatim*, not `min(timeout, left)` —
  by design, so a generous explicit budget is never silently re-capped
  (see its docstring). That meant passing `timeout=min(local_cap,
  local_remaining)` alone did nothing once a scan deadline was active: a
  hung `clang -M` include-map call would still get the *full* remaining
  scan budget instead of stopping at this extractor's own ~30s
  aggregate/120s per-unit ceiling. Each call now runs inside a narrower
  nested `deadline.deadline_scope()` bound to whichever is tighter — the
  local cap or the outer scan deadline — so a `--budget 30m --depth
  source` scan can no longer have this advisory pass consume the whole
  budget on one stuck compile unit.
- Same `run_bounded()`-ignores-`timeout=`-under-an-active-deadline gap,
  found in the two remaining auxiliary probes with a local cap smaller
  than 120s: `dumper_sysinc._probe_gnu_system_includes` (the castxml↔clang
  system-include parity probe, 15s local cap) and
  `build_query._is_gnu_make_launcher` (the GNU Make `--version` check,
  10s local cap). Both now run inside a nested `deadline.deadline_scope()`
  bound to whichever is tighter, same as the include-map fix above.
- **External SIGTERM can no longer orphan a detached compiler process
  group on the plain CLI/CI path.** `deadline.run_bounded()` deliberately
  detaches its child into its own session (`start_new_session=True`) so a
  timeout *it detects itself* can kill the whole group — but that
  detachment also shields the child from an *external* SIGTERM sent to
  abicheck's own process (job-scheduler cancellation, a CI step's own
  timeout): Python's default SIGTERM disposition exits immediately
  without running `run_bounded`'s own `except`/`finally` cleanup. New
  `deadline.install_sigterm_cleanup()` (called once from `cli.main`)
  tracks every in-flight process group and, on SIGTERM, best-effort
  SIGKILLs all of them before re-raising SIGTERM at itself so the process
  still exits with normal signal semantics. Mirrors the MCP path's
  existing `service_scan._kill_process_tree` outer watchdog, which the
  plain CLI/CI path had no equivalent of (Codex review, PR #591).
- Closed the same `run_bounded()`-ignores-`timeout=` gap in
  `dumper._castxml_version_note` (the `castxml --version` upgrade-note
  probe, 15s local cap) — the last remaining instance. Also refined its
  round-2 fix: a `DeadlineExceeded` now propagates only when the *outer
  scan* deadline (not this probe's own local cap) was the binding
  constraint, so hitting the local cap alone under a generous `--budget`
  still degrades to `""` like an ordinary probe failure, while a genuine
  scan-budget overflow still propagates as before. Split out of
  `dumper.py` (at the file-size hard cap) into `dumper_castxml_probe.py`,
  re-exported so the public `dumper._castxml_version_note` surface is
  unchanged — the same split-and-reexport pattern `dumper_sysinc.py`/
  `dumper_clang_errors.py` already established earlier in this PR.
- Fixed a race in `deadline._kill_process_tree`: it looked up the target
  process group via `os.getpgid(proc.pid)`, which can fail once the
  direct child has already exited (e.g. a wrapper that backgrounds the
  real compiler and exits itself) — even though the process group itself,
  with the still-running backgrounded child, is very much alive. Since
  `run_bounded`'s `start_new_session=True` makes the child's pid *equal*
  to the pgid for the group's entire lifetime (a POSIX process group's id
  never changes, even after its leader exits, as long as a member
  remains), the lookup was unnecessary and actively harmful: on failure
  it fell back to killing only the already-dead direct process, leaving
  the backgrounded compiler orphaned. Now uses `proc.pid` directly as the
  pgid, no lookup (Codex review, PR #591).
- Same race, same fix, in `deadline._register_pgroup` (the
  `install_sigterm_cleanup` registry populated right after `Popen()`
  returns): a fast wrapper backgrounding the real compiler and exiting
  immediately could make its `os.getpgid(proc.pid)` lookup fail too,
  silently skipping registration — an external SIGTERM would then find no
  tracked group for that (still-alive) backgrounded compiler at all. Now
  uses `proc.pid` directly here as well.
- `deadline._active_pgroups_lock` is now a `threading.RLock()`, not a plain
  `Lock()`. Python only delivers signals on the main thread; if a SIGTERM
  arrives mid-`_register_pgroup`/`_unregister_pgroup` while that thread
  already holds the lock, `install_sigterm_cleanup`'s handler re-entering
  the same lock on the same thread would self-deadlock with a plain `Lock`
  (CodeRabbit review, PR #591).
- Closed the same local-cap-vs-scan-deadline classification gap (see the
  `include_graph.py`/`_probe_gnu_system_includes`/`build_query.py` fixes
  above) in two more nested `deadline_scope()` call sites found on a
  further review pass: `buildsource.include_graph.ClangIncludeExtractor
  .extract_from_build`'s per-compile-unit `clang -M` probe now only
  aborts the remaining compile units when the *outer scan* deadline (not
  its own 120s per-unit cap) was the binding constraint — a local-cap
  timeout on one compile unit now logs and continues to the next instead
  of abandoning the rest of the include-map pass; and
  `build_query._is_gnu_make_launcher`'s GNU Make `--version` probe now
  re-checks the (by-then-restored) outer deadline after catching
  `DeadlineExceeded` from its nested scope, propagating only when the
  outer scan budget is truly exhausted rather than on every hit of its
  own 10s local cap (CodeRabbit review, PR #591).
- `service_scan._kill_process_tree` (the MCP worker-process outer
  watchdog) had a CRITICAL-severity gap: it only called `proc.terminate()`
  on the direct worker process when it found *no* detached descendant
  process groups. A worker that spawned a detached clang/castxml child
  (the common case — `run_bounded` always detaches its subprocess) but
  had not itself detached would then never be terminated at all on
  timeout — only its detached descendants were killed, leaving the worker
  itself running indefinitely. `proc.terminate()` on the direct worker now
  always runs, unconditionally (CodeRabbit review, PR #591).
- `dumper_clang_errors._parse_clang_ast_result` re-checks the scan
  deadline twice more it previously didn't: right after `json.load()`
  (before the potentially-large AST is cached via `_atomic_copy`) and
  again right after that cache copy completes (before returning) — a
  multi-hundred-MB cached AST's load/copy cost was previously invisible
  to `--budget` on this authoritative L2 path, mirroring the identical
  post-subprocess gaps already closed elsewhere in this pass (CodeRabbit
  review, PR #591).
- `tests/test_source_replay.py`'s pool-worker deadline-propagation test
  mutated a module-level list from inside the worker to observe
  `deadline.remaining()` — which only works under the default
  `ThreadPoolExecutor` backend; a `ProcessPoolExecutor` worker mutates its
  own private copy, leaving the parent's list empty and the test
  vacuously passing regardless of whether propagation actually worked.
  The worker now reports the observed value through its own return value
  (already threaded back through `_extract_cache_misses`'s result list),
  which the test reads directly — verified correct under both backends,
  including `ABICHECK_L4_EXECUTOR=process` (CodeRabbit review, PR #591).
- Two more warm-cache-hit deadline gaps closed, same shape as the earlier
  pre-parse cache-hit checks: the L2 clang header-AST cache hit
  (`_clang_header_dump`) re-checks the deadline before spawning nothing but
  *after* `json.loads()` — a huge cached AST's JSON parse can itself consume
  the rest of the budget, and the existing pre-load check doesn't see that.
  Same fix on the castxml side (`_castxml_dump`): re-checks after
  `DefusedET.parse()` of a large cached XML tree, before handing the root
  off to the rest of L2 processing (Codex review, PR #591, round 3).
- Same warm-cache-hit gap on the *fresh* (non-cached) castxml parse path:
  `_validate_castxml_output` re-checks the deadline after `DefusedET.parse()`
  of the just-produced XML tree, not only before it — a large fresh output
  can itself consume the rest of the budget between the pre-parse check and
  the caller receiving the root (Codex review, PR #591, round 3).
- Two more instances of the local-cap-vs-scan-deadline classification bug
  closed, this time in a subtler shape than the earlier rounds: pre-computing
  "was the outer scan deadline or my own local cap what's binding" *before*
  the subprocess call is unreliable, because `run_bounded`'s own timeout
  escalation (SIGTERM → grace period → SIGKILL, plus a fixed 5s pipe-drain)
  can consume real wall-clock time *after* that snapshot was taken — enough
  to drain an outer scan budget that looked comfortably ahead of the local
  cap at entry. `dumper_castxml_probe._castxml_version_note` and
  `buildsource.include_graph.ClangIncludeExtractor.extract_from_build` now
  keep the existing entry-time pre-check (so the common, no-real-time-passed
  case stays correctly classified) but add a `deadline.check()` re-check
  after catching `DeadlineExceeded`, before falling back to "local cap only"
  — so genuine scan-budget exhaustion is never silently misreported as an
  ordinary per-call timeout just because it wasn't yet exhausted at the
  moment the call started (Codex review, PR #591, round 3).
- `source_replay._extract_cache_misses`'s opt-in `ABICHECK_L4_EXECUTOR=process`
  `ProcessPoolExecutor` workers now run `deadline.install_sigterm_cleanup` as
  their pool `initializer`. A process-pool worker is a genuinely separate OS
  process — it never inherits the SIGTERM handler `cli.main` installs in the
  main process (and fork-inherited handlers aren't guaranteed either, since
  the pool's start method can be "spawn"). Without this, an external SIGTERM
  landing on a worker mid-`run_bounded()` call would kill it via Python's
  default disposition, leaving its detached clang/castxml process group
  untracked and orphaned — the exact failure mode this whole PR set out to
  close, just one layer further into the parallel L4 replay path (Codex
  review, PR #591, round 3).
- The same "check the deadline before, but not after, parsing a large
  output" gap closed on the two L4 source extractors, mirroring the L2
  fixes above: `CastxmlSourceExtractor.extract` now re-checks after
  `DefusedET.parse()` of a per-TU castxml XML file, before walking it in
  `_parse_root`; `ClangSourceExtractor.extract` now re-checks after
  `json.load()` of a per-TU clang AST, before walking it in
  `source_abi_from_clang_ast`/`_attach_source_edges`. Both fold into the
  existing `SourceExtractionError` contract (L4 failures degrade to partial
  coverage, never abort the scan, ADR-028 D3). The clang extractor's two
  deadline re-checks (before and after the load) now share a small
  `_recheck_deadline()` helper to keep the file under its 2000-line hard cap
  (Codex review, PR #591, round 4).
- Same "check before, but not after, the JSON load" gap closed on the L5
  call/type-graph fold: `call_graph.ClangCallGraphExtractor` and
  `type_graph.ClangTypeGraphExtractor` combined `json.loads(proc.stdout)`
  and the recursive `parse_clang_ast_calls`/`parse_clang_ast_types` walk
  into a single expression after only a pre-parse `deadline.check()` — a
  large L5 AST's JSON load could itself consume the rest of the budget,
  leaving the walk to run unbounded. The load and walk are now split with a
  `deadline.check()` between them, degrading to the existing advisory
  diagnostic+`[]` contract (ADR-028 D3) on overflow, same as the pre-parse
  check (Codex review, PR #591, round 4).
