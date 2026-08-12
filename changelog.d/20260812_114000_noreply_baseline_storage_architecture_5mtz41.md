<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Documentation

- `docs/reference/protect-committed-baseline.md`'s example workflow now
  includes `labeled`/`unlabeled` in its `pull_request` trigger types — the
  documented `bypass-label` approval/revocation flow depends on the label
  change itself starting a new run against the current head SHA, which the
  default trigger types (`opened`/`synchronize`/`reopened`) don't cover.
