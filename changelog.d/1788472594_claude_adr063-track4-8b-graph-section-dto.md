<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **A `"graph"` storage section now has its own typed DTO** —
  `abicheck.storage.graph_section_codec.GraphSection`, wired through
  `storage.dto.graph_to_dto`/`graph_from_dto`, replaces the generic
  `legacy_section_to_dto` pass-through envelope for the `"graph"` D8 legacy
  section. ADR-063 Track 4 (8B)'s second typed-DTO promotion beyond
  `semantic_ir` (after `"types"`); the on-disk section payload shape is
  unchanged.
