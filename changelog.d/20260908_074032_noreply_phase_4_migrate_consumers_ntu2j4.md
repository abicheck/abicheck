<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- **`compare` is now the recommended workflow for source/build-evidence
  comparisons against a real baseline.** `docs/start/choose-your-workflow.md`,
  `docs/use/scan-levels.md`, and `docs/use/github-action-source-scans.md` now
  lead with `abicheck compare` instead of `abicheck scan` for that case,
  since `compare`'s pipeline already runs the same cross-source checks,
  pattern pre-scan, and `--depth`/`--since`/`--changed-path`/`--sources`/
  `--build-info` evidence collection `scan` does. **Single-build audits
  (no prior release) stay on `scan`** — `compare --no-baseline` is not a
  safe replacement for that scenario: verified it crashes with an unhandled
  `AssertionError` instead of reporting a finding when the candidate
  genuinely has a hygiene problem, at any depth including binary/
  header-only, not just when build/source evidence is involved. `scan`
  remains a fully supported command and these pages still point to it for
  its several remaining genuine gaps: `--budget`, `--crosscheck`'s
  `KEY=error` promotion syntax, `--build-target`, risk-driven
  `--depth auto`, single-build/no-baseline audits (the crash above), and
  (for the Action) `mode: scan`'s internal translation to `compare` for a
  baseline comparison, which turned out not to be safely achievable at all
  — `compare`'s automatic cross-source-checks stage has no way to disable
  itself or reproduce `scan`'s own advisory-only stripping of
  single-version hygiene findings, at any depth.
