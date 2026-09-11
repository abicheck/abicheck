<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Internal-only**: `abicheck/buildsource/entity_identity.py` and
  `abicheck/buildsource/entity_resolver.py` (back-compat re-export facades
  for `abicheck.model.entity_identity`/`abicheck.model.entity_resolver`)
  now declare `__all__` and are registered in `architecture/modules.yaml`'s
  `facades` list, so the repository's delegation-only facade check
  (`scripts/check_architecture.py`) enforces them too. No behavior change.
