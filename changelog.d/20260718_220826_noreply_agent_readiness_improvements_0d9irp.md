### Documentation

- **Documented the MCP/Python-API depth-contract gap** — `abicheck/mcp_server.py`'s
  `_validate_public_depth` now notes that it validates only the *spelling* of
  a requested `depth`, not whether that depth was actually reached; unlike
  the CLI's `dump --depth` (once PR #601 lands), neither the MCP tool surface
  nor `abicheck/service.py`'s `ScanRequest`/`run_scan_subprocess` hard-fail on
  an unsatisfied depth. Tracked as acknowledged remaining work in `AGENTS.md`.
