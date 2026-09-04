<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`TYPE_VTABLE_CHANGED` no longer fabricates a vtable-removed finding
  when comparing against a PDB-derived snapshot.** PDB's real extractor
  never captures vtable data for any record, which previously let an
  unrelated `size_bits` delta (or a cross-format Itanium-vs-MSVC
  mangling mismatch) drive a fabricated `TYPE_VTABLE_CHANGED` BREAKING
  finding reporting the PDB side's vtable as confirmed-empty rather than
  simply unknown. `compare.vtable_evidence.vtable_transition_is_evidenced`
  (shared by `diff_types_vtable.py`'s `TYPE_VTABLE_CHANGED` detector and
  `diff_cxx_rules.virtual_method_addition`) now declines outright whenever
  either side's `vtable_fact` is not `PRESENT`/`PARTIAL`, before either
  fallback evidence stream runs. This is disjoint from, and does not
  change, the existing DWARF per-TU capture-gap heuristic (`Fact.
  present([])` on that path stays `PRESENT`, so it never trips the new
  check) — the fix is scoped to backends that never capture vtable data
  at all, which today is only PDB.
