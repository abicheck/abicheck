### Removed

- Deleted twelve delegation-only compatibility facades whose implementation
  already had a canonical ADR-061 owner, and migrated every caller to that
  owner: `abicheck.aggregate`, `abicheck.aggregate_findings`,
  `abicheck.aggregate_manifest`, `abicheck.api_types`,
  `abicheck.bundle_facts`, `abicheck.bundle_facts_serialization`,
  `abicheck.bundle_facts_store`, `abicheck.contract_coverage_ledger`,
  `abicheck.qualified_name_segments`, and the three
  `abicheck.buildsource.entity_identity`/`entity_resolver`/
  `source_graph_query` re-export modules. These import paths no longer
  resolve; import the owning module (or, for the typed request/result
  types, the supported `abicheck.service` surface) instead.
- Retired `CompareRequest.env_matrix_path` and its
  `effective_env_matrix()`/mutual-exclusion plumbing, along with
  `ResolvedComparePair.resolved_env_matrix` and its "unresolved" sentinel.
  `CompareRequest.env_matrix` carries the already-resolved
  `EnvironmentMatrix` (from `.abicheck.yml`'s `deployment:` key) and is the
  one representation; nothing loads a matrix file at classify time any more.
- Dropped the unread `audit_suppressions`/`suppress` parameters from
  `compare`'s manifest preflight.

### Changed

- `DEFAULT_MAX_JSON_OBJECT_NODES` now lives with the rest of the bundle-facts
  model in `abicheck/model/bundle_facts.py`, so the CLI and Action-config
  layers read it without a `frontends -> storage` dependency.
- `scripts/catalog_subjects.py` and `scripts/catalog_classification.py` use
  the shared strict-YAML loader (`abicheck.model.yaml_strict`) instead of
  keeping their own duplicate-key `SafeLoader` subclasses. Same accepted
  input, same `ValueError` contract.
