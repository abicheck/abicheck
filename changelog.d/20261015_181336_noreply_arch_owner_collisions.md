<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Bare module names are unique across the ADR-061 owner packages** — 34 modules whose basename was shared by two or more owner packages (for example `workflows/contracts.py` and `model/change.py` vs `compare/finding_identity/change.py`) were renamed to a name in their own owner's vocabulary, and every import, `mock.patch` target and architecture record was updated. No behaviour, schema or CLI change. Anyone importing one of the renamed internal modules directly must switch to the new path, for example `abicheck.workflows.contracts` is now `abicheck.workflows.service_requests`, and `abicheck.workflows.dump.pipeline` is now `abicheck.workflows.dump.request`.
