<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Internal-only**: `abicheck/buildsource/entity_identity.py`,
  `abicheck/buildsource/entity_resolver.py`, and
  `abicheck/buildsource/source_graph_query.py` (back-compat re-export
  facades over their `abicheck.model` counterparts) now declare `__all__`
  and are registered in `architecture/modules.yaml`'s `facades` list, so
  the repository's delegation-only facade check
  (`scripts/check_architecture.py`) enforces them too. No behavior change.
