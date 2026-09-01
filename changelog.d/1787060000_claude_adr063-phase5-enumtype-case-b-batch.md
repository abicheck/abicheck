### Added

- **ADR-063 Phase 5's fact/capability registry: `EnumType`'s case-(b)
  fields converted to `Fact[T]`** (schema v32) — `qualified_name`/
  `source_header` now carry `Fact[str | None]` siblings
  (`qualified_name_fact`/`source_header_fact`), the same case-(b)
  "`None` already unambiguously means not captured" pattern already
  applied to `RecordType`'s twin fields. Both header-AST backends
  construct `qualified_name_fact` explicitly as `Fact.present(...)`,
  matching `RecordType.qualified_name_fact`'s own convention: a `None`
  qualified name at global scope is a confirmed determination, not
  missing evidence. `provenance.tag_provenance()`'s existing
  `source_header_fact` fix (from the `RecordType` batch) generically
  covers `EnumType` too via its `hasattr` guard.

### Changed

- **`abicheck/serialization.py` split**: the ELF/PE/Mach-O/DWARF/SYCL/
  kABI/NumPy-C-API/Python-extension-and-API `*_from_dict` sub-block
  decoders moved to a new sibling leaf module,
  `abicheck/snapshot_platform_blocks.py` (mechanical extraction,
  unchanged function bodies) — kept as a flat root module rather than
  under `storage/`, since these decoders' parser dataclasses live in
  flat, unclassified parser modules and `storage`'s own
  `may_import: [model]` forbids a `storage -> extract` edge. This is
  purely a `architecture/debt.yaml` no-growth accommodation for the
  schema bump above; the public serialization surface is unchanged.
