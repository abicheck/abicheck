### Added

- Explicit cross-layer joins (evidence-entity-model Phase 2, invariants I2/I3).
  `abicheck/compare/export_join.py` joins the observed export tables (ELF,
  PE, Mach-O; read only through `model/export_index.py`) onto the
  declarations, and `abicheck/compare/debug_type_join.py` joins the observed
  debug types (DWARF, and BTF/CTF/PDB reduced to the same shape) onto the
  header records/enums, both keyed by the Phase 1 graph identity. Every
  subject lands in exactly one state -- `matched`, `ambiguous` (with its
  candidates), `unmatched`, or `unknown` when the other side was never
  observed -- so a public inline function without an export and an export
  without a declaration are both first-class orphans. The public-surface
  graph builder emits the observed `exports` and `debug_type_of` edges
  (evidence class `resolved_join`, described by `model/graph_join.py`'s
  `JoinSpec`); the `declares_linker_name` projection stays `derived`.
- The DWARF walk now records ODR conflicts: a further, layout-distinct
  definition another CU gave an already-seen struct/enum name
  (`DwarfMetadata.struct_odr_conflicts`/`enum_odr_conflicts`). The
  debug-type join reports such a name `ambiguous` rather than trusting the
  first definition. Snapshot schema is now **v51**; a pre-v51 snapshot loads
  with the observation marked "not looked for".

### Changed

- `export_surface.py` (`--contract exports`) and the public-surface
  closure's undeclared-export seeding now read the `exports` join instead of
  their own private matchers. An export that no declaration's linker name
  names is "undeclared" even when an unrelated declaration shares its bare
  name (`foo` next to `ns::foo`), which the old lookup-key match treated as
  declared.
- The DWARF layout tier (`diff_platform._diff_dwarf`) and the depth
  projection's DWARF pre-scope decide which debug types to diff from the
  debug-type join (`compare/debug_type_scope.py`) instead of a bare-name
  rule. A private debug type that merely shares its last `::` segment with a
  public header type (`impl::Foo` beside `api::Foo`) is no longer diffed,
  and so no longer appears as a filtered out-of-surface finding.
