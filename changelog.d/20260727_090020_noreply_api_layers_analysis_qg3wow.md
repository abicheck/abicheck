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
