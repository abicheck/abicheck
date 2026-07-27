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
