<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->

### Removed

- **The composite GitHub Action no longer reconstructs a verdict, gate, or
  orthogonal-axis contribution from rendered prose** (ADR-063 Phase 6, Track
  T8). `action/run.sh` reads the structured `run_outcome`/JSON report
  contract and nothing else: `_report_compat_verdict` dropped its SARIF
  `runs[0].properties.abiVerdict` lookup and its `sed` over a rendered
  markdown/text report's `Verdict:`/`**Verdict**` line, `_severity_gate_exit`
  dropped its `sed` over the CLI's `severity gate: exit N ... blocking:`
  line, and `_coverage_gated`/`_assurance_gated` dropped their
  `$STDERR_CONTENT` greps. With no readable JSON report those readers now
  answer "no signal" rather than guessing, so the verdict the process exit
  code established is published unchanged — the exit code being the one
  transport-level fallback that stays, alongside the unchanged Click
  usage-error detection. `fail-on-*` remains step policy that never rewrites
  the reported verdict. Runs whose report is genuinely unreadable (notably
  `format: text`/`markdown`/`sarif` invocations that write no JSON sidecar)
  therefore no longer receive the `SEVERITY_ERROR`/`COVERAGE_INCOMPLETE`/
  `ANALYSIS_INCOMPLETE` labels or the prose-derived verdict escalation they
  previously got; use a format that produces a JSON report to keep them.
