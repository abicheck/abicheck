### Fixed

- A platform block whose export table was never parsed (a default or
  parse-failed `ElfMetadata()`: no symbols and no `machine`) is no longer
  read as a library exporting nothing (evidence-entity-model I4). It used to
  make `FUNC_DELETED_ELF_FALLBACK` report every old export as BREAKING,
  flag every declaration `PUBLIC_NOT_EXPORTED` at HIGH confidence, and strip
  every declaration from a `--depth binary` projection. A DWARF `= delete`
  member is suppressed as "never exported" only when the old side's table
  was read too.
- "Exported but undeclared" contract conflicts and the undeclared-export
  seeding of the public surface now require the library's own headers to
  have been parsed; a side with no header AST no longer reports every export
  as undeclared.
- `CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED` is no longer reported from
  a narrowed, degraded, or header-only call-graph pass, whose missing
  callees prove nothing.

### Added

- Compare reports carry `edge_coverage` (report schema 5.4) and a
  **Relationship coverage** section in Markdown and HTML: per side, the
  relationships (exports, debug types, header declarations, type references,
  source-graph edges) whose absence could not be proven, with the producer
  status behind each.
