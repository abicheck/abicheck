<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--contract-evaluation` `--help` text no longer says SARIF/HTML never
  render `contract_relevance`** — both formats have annotated an excluded
  finding with a `contractRelevance` property (SARIF) or rendered it in a
  not-evaluated section (HTML) for a while; only JUnit still doesn't carry
  it. `docs/reference/cli-reference.md` regenerated to match.
