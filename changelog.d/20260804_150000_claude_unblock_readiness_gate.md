<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Restored the AI-readiness gate to zero errors**, which had been failing
  every PR in the repository regardless of its contents. Two unrelated
  violations had landed together: a now-removed module had grown past the
  gate's **hard** 2000-line cap (which has no allowlist), and
  `docs/use/output-formats.md`'s example still showed
  `"report_schema_version": "2.26"` after the source of truth moved to
  `2.27`. The `ai-readiness` CI job additionally stops before its own
  `pip install -e ".[dev]"` step when the gate fails, so the per-tier
  accuracy step that follows it — which carries `if: always()` — then died
  on `ModuleNotFoundError: No module named 'elftools'`; that was a
  consequence of the first failure, not a third problem, and clears with it.
