# `abicheck report` Action

Renders a Markdown summary from already-produced `check-target` component JSON
reports. It does not run analysis, call GitHub APIs, or need `GITHUB_TOKEN`.

```yaml
- uses: abicheck/abicheck/actions/report@<pinned-sha>
  with:
    reports-dir: downloaded-reports
    manifest: run-plan.json
    summary-file: abicheck-report-summary.md
```

Use `discovered-only: 'true'` instead of `manifest` only when there is no
expected target inventory to enforce. Inputs are staged as bounded regular
files before canonical aggregate loading; report count, input-byte, and
summary-byte limits are configurable through the Action inputs. The Action
publishes the canonical aggregate exit code as an output but is report-only:
it does not fail the job for that code.
