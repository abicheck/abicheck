<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Documentation

- **Fixed a batch of factual inconsistencies found in a documentation
  review** — the MCP narrative now correctly distinguishes `not_comparable`
  from success/error and describes the scoped/severity-aware exit-code
  interaction accurately; GitHub Action examples pin the confirmed `v0.5.0`
  release tag instead of an unpublished `v1`; the source-replay scenario and
  `dependency-source` docs correctly state that only `conda-forge-clang20`
  (not plain `conda-forge`) provisions `clang`; the platform support matrix
  now flags that "Full" tracks implemented capability, not per-toolchain
  CI maturity; `--ast-frontend auto`'s fallback behavior is described consistently
  (opt-in only, via `--allow-ast-frontend-fallback`); the CLI-surface docs
  distinguish the five core per-library commands (`compare`, `compat`,
  `deps`, `dump`, `scan`) from the project-orchestration commands
  (`aggregate` and the `project` group — `project plan`, `project
  validate`, `project validate-build`, ADR-054); stale "being updated
  in parallel" placeholders and internal plan-tracking prose were removed;
  the GitHub Action app-compatibility examples keep the `extra-args:
  '--used-by ...'` form (the dedicated `used-by` input postdates the
  `v0.5.0` release these examples are pinned to) with a note pointing at
  the newer input for a commit-SHA pin; and seven example-catalog READMEs
  (`case02`, `case07`-`case11`, `case14`) had their verdict banner corrected
  from a stale "ABI CHANGE" label to match their actual `BREAKING`
  ground-truth verdict.
