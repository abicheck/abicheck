<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **GitHub Action warns on mode-scoped inputs set on an incompatible mode**
  — `debug-info1`/`debug-info2`, `devel-pkg1`/`devel-pkg2`, `dso-only`,
  `include-private-dso`, `keep-extracted`, `fail-on-removed-library`,
  `jobs`, `abi-baseline`, `estimate`, and `audit` are each only
  forwarded/consumed by a subset of `mode` values; setting one on an
  incompatible mode used to be a silent no-op. `action/validate-inputs.sh`
  now emits a `::warning::` job annotation (the step still succeeds) for
  these legal-but-inert combinations, as part of G30's onboarding-blocker
  fixes (ADR-047 P0.1).
