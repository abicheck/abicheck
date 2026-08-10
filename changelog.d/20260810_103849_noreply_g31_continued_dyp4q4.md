<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **DWARF (`--depth debug`/binary-only) vtable-pointer offset is now read
  from the compiler's own debug info instead of assumed to be 0.** Every
  polymorphic `RecordType`'s `vptr_offset_bits` previously used a
  `0 if vtable else None` heuristic that missed a real case: a class whose
  own vtable is entirely inherited (it adds or overrides no virtual method
  of its own) has an empty DWARF-visible `vtable` list, so it was reported
  as `None` (unknown) even though it genuinely has a vtable pointer at a
  real, resolvable offset. `vptr_offset_bits` is now read from GCC/Clang's
  artificial `_vptr.<Class>`/`_vptr$<Class>` debug-info member directly,
  falling back to the resolved primary base's own already-known offset for
  a class with no local vptr member of its own, and to the original
  `0`-if-polymorphic heuristic (extended to also recognize a class
  polymorphic only through an already-known-polymorphic virtual base) for
  the residual case neither mechanism can otherwise explain — a "nearly
  empty" virtual base sharing its vptr slot with no local member of its
  own (G31 Phase C). This DWARF-derived value also
  reaches castxml/clang snapshots via `dumper_layout_backfill.py`'s
  existing layout backfill, so the whole-snapshot disk cache version was
  bumped (v8 → v9) to invalidate a warm cache holding the old, less-accurate
  backfilled value.

