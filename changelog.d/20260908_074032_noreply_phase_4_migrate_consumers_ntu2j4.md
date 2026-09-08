<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- **`compare` is now the recommended workflow for source/build-evidence
  comparisons and single-build audits.** `docs/start/choose-your-workflow.md`,
  `docs/use/scan-levels.md`, `docs/use/github-action-source-scans.md`, and
  `docs/integration/scenarios/single-build-audit.md` now lead with
  `abicheck compare` (including `compare --no-baseline` for an audit with no
  prior release) instead of `abicheck scan`, since `compare`'s pipeline
  already runs the same cross-source checks, pattern pre-scan, and
  `--depth`/`--since`/`--changed-path`/`--sources`/`--build-info` evidence
  collection `scan` does. `scan` remains a fully supported command and these
  pages still point to it for its few remaining genuine gaps: `--budget`,
  `--crosscheck`'s `KEY=error` promotion syntax, `--build-target`, risk-driven
  `--depth auto`, and (for the Action) a no-baseline `mode: compare` input,
  plus build/source evidence (`--sources`/`--build-info`) on
  `compare --no-baseline`, which that CLI slice doesn't accept yet.
