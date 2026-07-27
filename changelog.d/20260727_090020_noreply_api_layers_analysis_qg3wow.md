### Fixed

- **`abi_deps`/`abi_aggregate`/`abi_project_validate`/`abi_project_plan` now
  honor ADR-021b's per-invocation timeout and input-size guards** — the
  four MCP tools added in the prior commit ran the CLI-equivalent work
  unbounded; each now runs its blocking work in a thread bounded by
  `ABICHECK_MCP_TIMEOUT` (returning a structured timeout error, same as the
  original four tools) and checks every file it reads (binary, per-target
  reports, manifest/run-plan, config, toolchain-bindings, build-output)
  against `ABICHECK_MCP_MAX_FILE_SIZE` before processing it.
- **mypy: `abicheck.mcp_server_project`'s `@mcp.tool()` decorators no longer
  trip `disallow_untyped_decorators`** — the existing `pyproject.toml`
  override for FastMCP's untyped decorators was scoped to
  `abicheck.mcp_server` only; extended to cover the new sibling module too.
- **`tests/test_mcp_server_deps_aggregate_project.py::TestAbiDeps::
  test_resolves_a_real_elf_binary` now skips on non-Linux** — `abi_deps`
  wraps an ELF-only resolver; macOS/Windows CI runners have no ELF binary at
  the well-known paths the test probed (macOS system binaries are Mach-O).
- **`abi_deps`/`abi_aggregate`/`abi_project_validate`/`abi_project_plan` now
  observe a running server's `--timeout`/`--max-file-size`/`--log-format`
  overrides** — these four tools previously read their own local
  `ABICHECK_MCP_TIMEOUT`/`ABICHECK_MCP_MAX_FILE_SIZE` env-var snapshots and
  always logged in plain text, so an operator reconfiguring a running server
  via CLI flags silently didn't reach them. `abicheck/mcp_shared.py` is now
  the single source of truth for `MCP_TIMEOUT`/`MCP_MAX_FILE_SIZE`/the
  structured-logging flag, and every tool module reads it module-qualified
  (`mcp_shared.MCP_TIMEOUT`) so a CLI-flag override reaches all eleven tools
  uniformly.
- **The new tools' timeout guard no longer blocks past its own deadline** —
  `_call_with_timeout` used `with ThreadPoolExecutor(...) as pool:`, whose
  `__exit__` calls `shutdown(wait=True)` and therefore still waited for a
  genuinely stuck worker to finish even after `future.result(timeout=...)`
  had already raised. Now shuts the pool down with `wait=False` in a
  `finally` instead.
- **`abi_project_plan` now counts config validation against `--timeout` too**
  — `validate_project_targets`/`check_profile_bindings_resolve` previously
  ran unbounded before the timeout-wrapped `generate_run_plan` call, so a
  large but size-compliant config could stall past the advertised
  per-invocation deadline. Both now run inside the same bounded worker.
- **`abi_deps` now validates the file a sysroot actually resolves to** —
  when `sysroot` is supplied, `resolver._seed_root` parses the sysroot-
  rebased path, not the host-absolute `binary_path` passed in; the
  existence/format/size pre-checks previously validated the wrong file, so
  a valid host ELF could authorize an oversized or non-ELF file under the
  sysroot. `abi_deps` now mirrors `_seed_root`'s own rebasing logic before
  checking.
- **`abi_deps` now bounds every dependency it resolves, not just the root
  binary** — `resolver.resolve_dependencies` parsed each transitively
  resolved DSO (via `search_paths`/`ld_library_path`/`sysroot`/default
  search order) with no size guard, so a small root binary could still
  make `abi_deps` parse an arbitrarily large dependency file. Added an
  optional `max_file_size` parameter (default `None`, preserving prior
  behavior for the CLI `deps tree` command and all other callers) threaded
  through `resolve_dependencies`/`stack_checker.check_single_env`; `abi_deps`
  now passes `ABICHECK_MCP_MAX_FILE_SIZE`.
- **The MCP server's `instructions` string now lists all eleven registered
  tools** — an MCP client reads this text to pick a tool; it previously
  named only four of the original seven, omitting `abi_audit`/`abi_scan`
  entirely and all four tools this PR added.
- **`abi_deps`'s new size guard no longer leaks a filesystem path in its
  error message** — `resolver._check_dso_size`'s `ValueError` embedded the
  full resolved path of an oversized dependency; now names only its
  basename, matching `mcp_shared._check_file_size`'s existing label-only
  (no-path) contract for MCP error responses.
- **A relative `binary_path` is no longer wrongly rebased under `sysroot`**
  — `abi_deps` pre-resolves every path argument to an absolute one for
  path-safety, which erased the relative/absolute distinction
  `resolver._seed_root`'s sysroot rebasing depends on; a relative
  `binary_path` combined with `sysroot` (the `deps tree ./app --sysroot
  ...` pattern the CLI itself documents) was silently rebased under
  `<sysroot>/<absolutized-cwd>/...` instead of being resolved against cwd
  like the CLI does. `abi_deps` now tracks the caller's original
  relative-vs-absolute input separately from the path-safety-resolved copy.
- **MCP test-isolation fixtures now restore all three `abicheck.mcp_*`
  package attributes, not just `mcp_server`'s** — importing
  `abicheck.mcp_shared`/`abicheck.mcp_server_project` under a temporary
  mock also sets the corresponding attribute on the already-loaded
  `abicheck` package object; popping `sys.modules` alone left that
  attribute pointing at the mock, so a later `from abicheck import
  mcp_shared` (etc.) could resolve to a stale mocked module without
  re-importing at all. Fixed in both `tests/test_mcp_reference.py`'s
  isolation fixture and `tests/test_cov95_misc.py`'s import helper.
- **A relative `search_paths` entry is no longer wrongly rebased under
  `sysroot`** — same root cause as the `binary_path` fix above:
  `resolver._build_search_order` joins a relative search path directly
  onto `sysroot` (`<sysroot>/lib`), but `abi_deps` had already
  absolutized every entry via `_safe_read_path`, so that join produced
  `<sysroot>/<absolutized-cwd>/lib` instead — silently making a search
  path that resolves fine through `deps tree` report as unresolved
  through `abi_deps`. Each entry's relative-vs-absolute form is now
  preserved the same way `binary_path`'s is.
- **`abi_aggregate` now counts expected-set parsing against `--timeout`
  too** — `_resolve_expected` (manifest/run-plan reading) previously ran
  unbounded before the timeout-wrapped `aggregate_reports_dir` call, so a
  slow manifest/run-plan read could stall past the advertised
  per-invocation deadline. Both now run inside the same bounded worker.
- **A missing `reports_dir` no longer turns a full build-matrix outage
  into a generic tool error** — `aggregate.collect_reports` deliberately
  treats a nonexistent reports directory as zero reports, so
  `aggregate_reports_dir` can still return a structured
  required-coverage failure (exit code 1) instead of a hard error. The
  MCP wrapper's own directory check now only rejects a path that
  *exists* but isn't a directory — this is an intentional MCP-specific
  improvement over `collect_reports`'s own behavior (which treats
  "missing" and "exists but is a file" identically as zero reports) and
  over the CLI's `aggregate --reports-dir`, which has no Click-level
  type check at all; a prior version of this entry described the added
  check as "matching" the core `collect_reports` contract, which
  overstated it — there is no existing contract distinguishing those two
  cases for this check to match (self-review finding).
- **`abi_deps`'s sysroot rebasing no longer treats a sibling directory as
  already-under-sysroot** — both `_resolve_sysroot_path` (the MCP
  pre-check mirror) and `resolver._seed_root` itself used a raw string
  prefix check (`str(binary).startswith(str(sysroot))`), so an absolute
  binary under `<sysroot>-other/...` was wrongly treated as already
  rebased and left unrebased, letting the pre-check and the actual parse
  validate a path outside the sysroot the caller configured. Both now use
  `Path.is_relative_to()`, matching them to each other and closing the
  false-negative.
- **`abi_project_validate`/`abi_project_plan` now count config/build-output/
  bindings parsing against `--timeout`** — `_load_project_targets_config`,
  `_parse_build_output_specs`, and `load_bindings_file` previously ran
  synchronously before the timeout-wrapped validation/generation call, so a
  slow-to-read config, build-output manifest, or bindings file could stall
  past the advertised per-invocation deadline. All three now run inside the
  same bounded worker as the call that follows them.
- **`abi_aggregate` now counts report-directory discovery against
  `--timeout`** — `_check_dir_json_file_sizes`'s `*.json` glob+stat over
  `reports_dir` previously ran before the timeout wrapper started; a very
  large or stalled reports directory could stall past the deadline even
  though expected-set parsing and aggregation were already bounded. Moved
  inside the same bounded worker.
- **`abi_project_validate`/`abi_project_plan`/`abi_aggregate` no longer leak
  local filesystem paths in parser error messages** — `click.UsageError`s
  raised by the shared CLI config/build-output/bindings/manifest/run-plan
  parsing helpers embed the full resolved path for a human terminal reader
  (e.g. `"/home/user/private/.abicheck.yml must contain a YAML mapping"`);
  these three tools previously returned that message verbatim, bypassing the
  sanitized-error envelope every other MCP error path uses. A new
  `_redact_paths` helper replaces every path this tool itself resolved with
  just its basename before the message reaches the caller.
- **`abi_deps` now counts its existence/format/size preflight against
  `--timeout`** — a FIFO or a stalled filesystem could previously block on
  `effective_path.exists()`/`_detect_binary_format`/`_check_file_size`
  before `_call_with_timeout` was even reached, so `ABICHECK_MCP_TIMEOUT`
  never got a chance to return the advertised structured timeout for that
  I/O. Moved inside the same bounded worker as `check_single_env`.
- **`abi_deps` no longer resolves a host symlink before rebasing an absolute
  path under `sysroot`** — `_safe_read_path` always follows symlinks, so an
  absolute `binary_path`/`search_paths` entry that traverses one (e.g. a
  merged-`/usr` host's `/lib` -> `/usr/lib`) was rebased under
  `<sysroot>/usr/lib/...` instead of the resolver/loader-semantic
  `<sysroot>/lib/...` `resolver._seed_root`/`_build_search_order` actually
  parse — potentially validating the wrong file or reporting a false
  "not found". The sysroot rebase now applies to the raw, un-resolved
  absolute path, matching how the CLI's own non-resolving Click `Path`
  handling behaves.
- **`abi_project_validate`/`abi_project_plan`/`abi_aggregate` now count
  their remaining single-file existence/size preflight checks (config,
  manifest, run-plan, toolchain-bindings, build-output) against
  `--timeout` too** — each was still a synchronous `stat()`/existence check
  before `_call_with_timeout` started; a stalled filesystem could block on
  any single one of them with no chance of the advertised structured
  timeout, even though the directory-wide and parsing steps around them
  were already bounded. All moved inside their tool's bounded worker,
  alongside a new shared `_ToolPreflightError` (generalizing `abi_deps`'s
  `_BinaryProbeError`) so a plain "config file not found"/"Binary file not
  found" preflight result can't be conflated with an unrelated
  `ValueError`/`FileNotFoundError` the wrapped operation itself might raise.
- **`abi_aggregate`'s "reports_dir is not a directory" error no longer
  leaks the full path** — it returned the caller-supplied `reports_dir`
  string verbatim instead of going through the same basename-only
  convention every other path-leak fix in this PR uses.
- **`abi_aggregate` now counts `reports_dir`'s own path resolution and
  type check against `--timeout`** — `_safe_read_path(reports_dir)`'s
  symlink-following `.resolve()` call and the `exists()`/`is_dir()` check
  ran before `_call_with_timeout` started; a stalled NFS/FUSE mount could
  block on either with no chance of the advertised structured timeout,
  even though report discovery and aggregation right after them were
  already bounded. Both moved inside `_do_aggregate`.
- **`abi_deps` now rejects a missing/typo'd `search_paths` entry with a
  clear error instead of a falsely-unresolved dependency** — the CLI's
  `deps tree --search-path` is declared `click.Path(exists=True)`, but the
  MCP tool passed every entry straight to the resolver with no existence
  check, silently reporting the dependency as unresolved instead of
  flagging the bad input. The existence check runs inside the same timed
  worker as the rest of `abi_deps`'s preflight, preserving each entry's
  original relative/absolute form for sysroot rebasing.
- **`mcp_shared._check_file_size`'s size-check `OSError` no longer leaks
  the checked path** — a real OS-raised `OSError` (e.g. a permission
  failure) carries the filename as a constructor argument, and `str(exc)`
  embeds it; since `_sanitize_error` surfaces `ValueError` messages
  verbatim, this bypassed the sanitized-error contract for every one of
  `_check_file_size`'s eight-plus call sites across all MCP tools. Now uses
  only `exc.strerror` (the human-readable message, no filename).
- **`docs/reference/environment.md`'s MCP timeout/file-size entries no
  longer contradict `docs/use/mcp-integration.md`** — they still named only
  `abi_dump`/`abi_compare` and `mcp_server.py` as the owning module; now
  they link to the authoritative "Runtime configuration" table instead of
  restating a stale subset, and correctly point at `mcp_shared.py`.
- **`abi_deps`'s `search_paths` existence check now validates the
  *effective*, sysroot-rebased location, not the raw host path** —
  `resolver._build_search_order` unconditionally joins *every* search-path
  entry (relative or absolute) onto an active `sysroot` (no
  already-under-sysroot guard, unlike the root binary's own rebase), so a
  directory that exists only inside the sysroot (e.g. `search_paths=
  ["lib"]` with `<sysroot>/lib` present but no host `./lib`) was wrongly
  rejected by the existence check added earlier in this PR. A new
  `_effective_search_path` helper mirrors that join exactly for the
  pre-check, while `resolve_dependencies` still receives each entry in its
  original relative/absolute form.
- **`abi_project_validate`/`abi_project_plan` now count `config`'s and
  `toolchain_bindings`' own path resolution against `--timeout` too** —
  `_safe_read_path`'s symlink-following `.resolve()` call for both paths
  ran before `_call_with_timeout` started; a stalled NFS/FUSE mount or a
  blocking symlink lookup could block on either with no chance of the
  advertised structured timeout, even though parsing/validation right
  after them were already bounded. Both moved inside their tool's bounded
  worker.
- **`abi_deps` now rejects a missing `sysroot` instead of silently ignoring
  it** — the CLI's `deps tree --sysroot` is declared `click.Path(exists=
  True)`, but the MCP tool accepted any string, so a typo'd or nonexistent
  sysroot could make a static binary appear to have resolvable dependencies
  under a directory that was never actually checked. The existence check
  runs inside the same timed worker as the rest of `abi_deps`'s preflight.
- **`abi_deps` now rejects an empty `search_paths` entry instead of silently
  resolving it to the current working directory** — an empty string turned
  into `Path("")`, which resolves to `.`, and was passed straight to the
  dependency resolver as a real search directory. Now raises a clear
  "Empty search_path is not allowed" error before it reaches the resolver.
- **`abi_aggregate` now accepts a `report_prefix` parameter** — the CLI's
  `aggregate --report-prefix` (default `abi-report-`) was never exposed on
  the MCP tool, so a caller using a custom report-file prefix had every
  report misclassified as an unexpected target and every required target
  reported missing. `abi_aggregate` now forwards `report_prefix` (default
  `aggregate.DEFAULT_REPORT_PREFIX`, matching the CLI) to
  `aggregate_reports_dir`.
- **`abi_deps`/`abi_aggregate`/`abi_project_validate`/`abi_project_plan`'s
  preflight-error responses are now audit-logged** — every other exit path
  (success, timeout, unexpected error) already called `_audit_log`; the
  `_ToolPreflightError` handler in all four tools returned its structured
  error without logging the attempt, leaving preflight rejections (missing
  file, bad format, oversized input) invisible to the audit trail.
- **`abi_aggregate`/`abi_project_validate`/`abi_project_plan`'s remaining
  domain-error handlers are now audit-logged too** — four more exception
  handlers (`abi_aggregate`'s `click.UsageError`/`AggregateError`/
  `ValueError` catch; `abi_project_validate`'s and `abi_project_plan`'s
  `click.UsageError`/`BindingsFileError` catch; `abi_project_plan`'s
  `_ProjectPlanValidationError` catch) had the same gap as the
  `_ToolPreflightError` handlers above but were missed in that pass — a
  malformed config, bindings file, run-plan, or build-output spec (arguably
  the most common real-world error class for these tools) produced a
  structured error response with no audit-log entry at all (code review
  finding). Fixed identically; regression tests added asserting
  `caplog`/`_audit_log` output for each of the four handlers.
- **`docs/integration/index.md` no longer misstates the Actions composition
  direction** — it claimed both the "Single-Action step" and "Reusable
  workflow" layers are "built out of" the "Primitive Actions" layer below.
  Verified against `actions/check-target/action.yml`'s "Run analysis" step:
  `check-target` (a primitive) itself checks out and invokes the root
  `abicheck/abicheck` composite Action directly, so the root Action actually
  sits underneath `check-target` in the real call graph — the reverse of a
  simple bottom-to-top ladder. Reworded to describe the actual graph
  (Codex review).
- **`abi_aggregate` now counts `manifest`'s and `run_plan`'s own path
  resolution against `--timeout` too** — `_safe_read_path`'s
  symlink-following `.resolve()` call for both paths ran before
  `_call_with_timeout` started; a stalled NFS/FUSE mount or a blocking
  symlink lookup could block on either with no chance of the advertised
  structured timeout, even though the size check and expected-set parsing
  right after them were already bounded. Both moved inside `_do_aggregate`,
  alongside `reports_dir`'s existing resolution.
- **`_redact_paths` no longer leaves a nested path partially redacted** —
  it substituted paths in caller-supplied order; when one resolved path was
  a literal prefix of another (e.g. a bindings file nested under a config
  directory), substituting the shorter one first rewrote it *inside* the
  longer path's own text too, leaving the longer occurrence only
  partially redacted (e.g. `"myproject/secretname"` instead of the fully
  redacted `"secretname"`). Now substitutes longest-first, independent of
  the order arguments are passed in.
