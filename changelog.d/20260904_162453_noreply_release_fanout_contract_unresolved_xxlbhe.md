<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`compare` on a directory or package (the release fan-out) can now use a
  `--pack` asserting `contract.unresolved`.** The release fan-out previously
  rejected any pack that assigned `contract.unresolved` with a hard
  `PackManifestError`, even when `--contract` was given. The per-library
  plumbing that field needs (a persisted contract context, merged with the
  pack's resolved config) already existed, so the rejection is gone: a pack
  asserting `contract.unresolved: warn` now applies uniformly to every
  library in the release, the same way `policy.overrides`/
  `surface.internal_namespaces` already do, zeroing the orthogonal
  contract-coverage exit contribution without hiding any
  `contract_coverage_failures` ledger entry. A pack asserting the field on a
  release comparison with no `--contract` is still rejected, as before,
  since nothing would read it.

