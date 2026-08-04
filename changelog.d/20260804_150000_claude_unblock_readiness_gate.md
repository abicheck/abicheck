<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Restored the AI-readiness gate to zero errors**, which had been failing
  every PR in the repository regardless of its contents. Two unrelated
  violations had landed together: `abicheck/mcp_server.py` had grown to 2017
  lines, over the gate's **hard** 2000-line cap (which has no allowlist), and
  `docs/use/output-formats.md`'s example still showed
  `"report_schema_version": "2.26"` after the source of truth moved to
  `2.27`. The `ai-readiness` CI job additionally stops before its own
  `pip install -e ".[dev]"` step when the gate fails, so the per-tier
  accuracy step that follows it — which carries `if: always()` — then died
  on `ModuleNotFoundError: No module named 'elftools'`; that was a
  consequence of the first failure, not a third problem, and clears with it.
  The verdict/exit-code/change-entry helpers move to a new leaf module
  `abicheck/mcp_server_verdicts.py`, re-exported from `mcp_server` so any
  existing import path keeps working. Behaviour is unchanged — the code is
  moved verbatim, and the module imports only `checker_policy` and
  `mcp_shared`'s logger at module scope (the heavier `severity`/`reporter`/
  `appcompat` imports stay function-local as they already were), so the edge
  from `mcp_server` stays one-directional and no import cycle forms.
