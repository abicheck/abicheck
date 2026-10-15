<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **Root modules moved next to their owners** — `abicheck.report_correlation` is now `abicheck.report.correlation`, `abicheck.provenance` is now `abicheck.extract.declaration_provenance`, and `abicheck.resolver` is now `abicheck.workflows.elf_dependency_resolver`. Each one's single owner layer (ADR-061) now holds the implementation; the old flat paths are removed, not re-exported. Callers importing the old paths must switch to the new ones.
