<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The advanced-DWARF extraction status survives a save** — a stored snapshot now keeps whether the advanced DWARF extraction completed, failed or did not run (`dwarf_advanced.extraction`, schema v68, ADR-080), so a baseline compared from its file reads the same advanced-DWARF coverage as the live run that wrote it. Before, every stored status read back as unknown and fell back to inferring from the payload. Snapshots written by v67 and earlier read as before. Because the content digest covers the document, DWARF-derived snapshot digests change once at this bump, and snapshot caches invalidate once. When a stored snapshot is compared with a live one whose status was recorded, the assurance output adds a mixed-provenance note; it changes no verdict or exit code.
