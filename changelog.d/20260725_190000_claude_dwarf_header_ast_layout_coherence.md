### Added

- **DWARF-vs-header-AST layout backfill now records its own coherence
  outcome instead of silently succeeding or failing.** When the clang L2
  header backend can't corroborate a header-declared record against DWARF
  debug info (kind mismatch, or no field/base overlap, despite a uniquely
  named DWARF candidate existing), that record's layout was already
  correctly left unbackfilled — but nothing surfaced *that this happened*,
  so a comparison could silently run with an incomplete layout on that
  record. `AbiSnapshot` gains `dwarf_layout_coherence` (`"matched"` /
  `"partial"` / `"mismatch"` / `"unavailable"`, `None` for the castxml
  backend where this check doesn't apply) and
  `dwarf_layout_coherence_mismatches` (the mismatched type names), bumping
  `SCHEMA_VERSION` to 16. A new RISK-tier `ChangeKind`,
  `HEADER_BINARY_CONTEXT_MISMATCH`, is emitted when either side of a
  comparison has `dwarf_layout_coherence == "mismatch"`, naming the affected
  record(s). It joins `compile_context_conflict` /
  `source_surface_dso_mismatch` in `semver.py`'s coherence-conflict gate, so
  a `BREAKING`/`API_BREAK` verdict co-occurring with a layout-coherence
  mismatch no longer produces a confident SONAME/MAJOR recommendation.
