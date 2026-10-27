<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A stored snapshot whose section schema version is newer than this abicheck is refused, not decoded** — a snapshot's `section_schema_versions` inventory (for example `binary` at 99 on a v1 section) was never checked against this build, so it loaded silently and was compared. It now fails with the same "upgrade abicheck" error the top-level `schema_version` check gives, from both the single-file snapshot reader and the directory-backed package reader. Older section versions still load when a registered migration reaches the current version, and fail up front otherwise.
