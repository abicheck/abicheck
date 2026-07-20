<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Canonical entity identity and safe graph-node reconciliation (G31 Phase
  B, ADR-048).** `abicheck.buildsource.entity_identity` computes a canonical
  identity for every L5 source-graph declaration/type node (compiler USR or
  real mangled name, else a normalized qualified-name signature, else a
  source-relative or synthetic fallback — never inventing a fact a producer
  didn't supply). `abicheck.buildsource.graph_reconcile` uses it to safely
  distinguish a genuine declaration rename/move from an unrelated add+remove
  pair across an old/new graph comparison, refusing to resolve on ambiguous
  evidence. New `ChangeKind`s `declaration_renamed`, `declaration_moved`,
  and `declaration_identity_reconciled` (RISK-tier, additive — never
  overriding or suppressing an artifact-proven finding). Flat findings with
  relevant L5 reachability evidence (starting with
  `public_api_internal_dependency_added`) now also carry structured
  `affected_public_roots`/`impact_proof_path`/`impact_is_direct` data
  (surfaced in JSON and SARIF), the machine-readable counterpart of the
  existing prose proof-path text.
