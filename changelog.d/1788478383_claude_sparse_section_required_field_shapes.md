<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`storage`'s sparse legacy-section DTOs now validate a required field's
  wire shape** — `BinarySection`/`DeclarationsSection`/`DebugSection`/
  `ProvenanceSection` previously accepted any value at all for a required
  field (e.g. `BinarySection.from_document({"elf": [], ...})`), freezing
  and round-tripping a malformed value unchanged; `serialization
  .snapshot_from_dict` would then read the wrong-shaped value as
  confirmed-absent, turning corrupted evidence into missing evidence.
  Each required field's own top-level shape (mapping-or-null / mapping /
  list / str, matching the real `AbiSnapshot` field) is now checked before
  freezing, shallow by design (matching `TypesSection`/`GraphSection`'s own
  precedent — only the container type is checked, never what's inside it).
