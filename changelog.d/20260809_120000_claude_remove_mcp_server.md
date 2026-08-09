### Removed

- **The MCP (Model Context Protocol) server and the `abicheck-mcp` entry
  point.** `abicheck/mcp_server.py` and its sibling modules
  (`mcp_server_inputs.py`, `mcp_server_project.py`, `mcp_server_scan.py`,
  `mcp_server_verdicts.py`, `mcp_shared.py`, `mcp_compare_receipt.py`), the
  `abicheck[mcp]` optional-dependency group, and the `abicheck-mcp` console
  script are gone. Agent and script integrations should use the CLI
  (structured JSON/SARIF/Markdown output) or the typed Python API
  (`abicheck.service`) directly — see [Python API](docs/use/python-api.md).
  `docs/contribute/adr/021-mcp-security-model.md` is kept as a historical
  record of the removed interface's design; its Status is marked
  `Deprecated — Retired`.
