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
  *exists* but isn't a directory, matching that contract.
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
