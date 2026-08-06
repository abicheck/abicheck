<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **CI: restored the `unit-tests` and `ai-readiness` jobs on `main`.**
  `abicheck/cli_options.py` and `abicheck/mcp_server.py` had drifted past
  the AI-readiness 2000-line hard cap. Split the ADR-037 D10 CLI-contract
  metadata (`FAMILY_FLAGS`/`COMPARE_FLAG_BUDGET`/`count_visible_options`)
  out of `cli_options.py` into a new leaf module,
  `abicheck/cli_options_contract.py`, and the `abi_estimate`/`abi_scan` MCP
  tools out of `mcp_server.py` into a new sibling module,
  `abicheck/mcp_server_scan.py` — both re-exported from their parent module
  for existing callers, mirroring the `cli_profiles.py`/
  `mcp_server_project.py` splits already used for the same reason. No
  behavior change. Also fixed two test-isolation gaps this surfaced:
  `tests/test_mcp_reference.py` and `tests/test_cov95_misc.py` restore a
  temporarily-mocked `mcp` package for a fixed list of `abicheck.mcp_server`
  sibling modules, which was missing the new `mcp_server_scan` module —
  without the fix, a later test could observe a stale, mock-bound
  `mcp_shared` instance and silently skip a file-size check.

