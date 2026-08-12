<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **The root Action's `abi-baseline` auto-fetch now also understands a
  release-contract baseline-set archive** (`abicheck-baseline-<profile>.tar.zst`,
  `publish-baseline.yml`'s format), not just a single
  `*.abicheck.json[.gz|.zst]` asset. New `baseline-profile`/`baseline-target`
  inputs select which contract profile and target to resolve from the
  archive when the original single-snapshot search finds nothing;
  `baseline-asset-name-template` (default `abicheck-baseline-{profile}.tar.zst`)
  matches a customized `publish-baseline.yml` asset name. Unifies
  the two previously-disjoint release-baseline protocols: a project can
  publish one multi-library baseline-set archive per release and have both
  `resolve-baseline`/`check-target` (the staged-baseline-path consumers) and
  the root Action's own `abi-baseline` (the auto-fetch consumer) resolve
  against it.
