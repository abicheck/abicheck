### Added

- **Ownership in the graph** (ADR-075 D5/D7, evidence-entity-model Phase 3).
  The public-surface graph gains `owned_by` and `in_contract` edges from each
  classified declaration/type to its owner and contract (`derived`, recomputed
  from the snapshot's recorded ownership, never persisted), and a release's
  `provided_by` relation maps each observed export to the members that
  provide it, over the release model's own `BundleExportIndex`
  (`resolved_join`).
- **Contract inputs are recorded with provenance**: the resolved
  configuration's `surface.ownership` (header roots, dependency roots, private
  headers/namespaces, `dependency_evidence`) says which layer stated each
  (`-H`, `.abicheck.yml`, or a typed `InputSpec.ownership`), and is persisted in
  the contract-context receipt when stated.

### Fixed

- **A declaration your headers carry but another owner provides is no longer
  your missing export.** `public_not_exported` (per library, one-member
  packages and release-wide alike) skips a declaration whose recorded contract
  is `private` or `external`, so a component's headers declared under
  `scope.dependencies` stop being demanded from a binary never meant to
  export them. Release member dumps and the release's acquired public surface
  are classified under the same project rules.
- **The contract receipt records the roots each side was classified under.**
  `compare`'s ADR-049 receipt names the `-H` directories of every side it
  extracted (none when both operands are stored snapshots, which ignore `-H`)
  and a typed request's own `ownership.rules.target_roots`, under
  `surface.ownership.header_dirs`.
