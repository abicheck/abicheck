### Fixed

- **Setting the new Action `build-target` input on `mode: compare`/`deps-tree`/
  `deps-compare` silently discarded it, with no warning.** `action/run.sh`
  only ever forwards `build-target` for `dump`/`scan` mode (the CLI flag it
  maps to exists on those two subcommands only). `action/validate-inputs.sh`
  already warns for the identical restriction on `public-header-dir`, but
  had no equivalent check for `build-target`. Fixed by adding the same
  mode-scoped warning.

### Documentation

- Registered a `build-target-scoping` topic in `docs/_meta/topics.yaml` for
  the new `scan --build-target`/Action `build-target` surface (P0.2), per
  `docs/AGENTS.md`'s "every new public-facing... Action input" registration
  rule — `learn/build-source-data.md`'s own "Scoping a Bazel query to
  specific root targets" section is the narrative home, with `fact_sources`
  pointing at the implementing modules.
