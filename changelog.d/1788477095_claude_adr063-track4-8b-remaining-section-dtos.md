<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Every D8 legacy storage section now has its own typed DTO** —
  `abicheck.storage.sparse_section_codec` adds `BinarySection`/
  `DeclarationsSection`/`LayoutSection`/`DebugSection`/`BuildSection`/
  `ProvenanceSection`, closing out ADR-063 Track 4 (8B)'s "typed DTOs for
  the remaining sections" goal (after `"types"`/`"graph"`). Each section's
  structurally-required fields (`elf`/`pe`/`macho`, `library`/`version`,
  etc.) become real, named, always-present attributes; every other,
  genuinely optional field is preserved exactly (never defaulted or
  dropped) in a validated `extra` mapping. On-disk section payload shape is
  unchanged. One real behavior change: `import_legacy_snapshot` now
  enforces a section's own required fields at import time, not only at
  export — a hand-built document missing a field every real
  `snapshot_to_dict()` output always includes (e.g. `provenance.version`)
  now fails to import rather than passing through silently.
