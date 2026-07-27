### Added

- **Four new MCP tools: `abi_deps`, `abi_aggregate`, `abi_project_validate`,
  `abi_project_plan`** — thin wrappers reusing the exact same non-Click logic
  as the matching `deps tree`, `aggregate`, `project validate`, and
  `project plan` CLI commands (`stack_checker.check_single_env`,
  `aggregate.aggregate_reports_dir`,
  `buildsource.project_targets.validate_project_targets`,
  `buildsource.run_plan.generate_run_plan`), so an MCP-driving agent can
  resolve a binary's dependency stack, fold per-target CI reports into one
  gate decision, and validate/plan a project's `.abicheck.yml` contract
  without shelling out. Split into a new sibling module
  (`abicheck/mcp_server_project.py`, mirroring the `cli_<name>.py` pattern)
  to keep `mcp_server.py` under the AI-readiness file-size cap; the shared
  `mcp` `FastMCP` instance and a few stateless path/error helpers moved to a
  new leaf module (`abicheck/mcp_shared.py`) so the split doesn't introduce
  an import cycle.

### Fixed

- **`tests/test_mcp_reference.py`'s tainted-module cleanup now also covers
  `abicheck.mcp_shared`/`abicheck.mcp_server_project`** — those two new
  modules (above) needed the exact same `sys.modules` purge-and-restore
  treatment the fixture already gave `abicheck.mcp_server`, or a prior
  test's mocked `mcp` package leaked through a stale cached `mcp_shared`
  import and made this file's "live MCP server" assertions fail with no
  tools found.
