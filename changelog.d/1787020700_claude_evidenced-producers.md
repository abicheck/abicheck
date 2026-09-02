### Fixed

- **A legacy PE/PDB snapshot still claimed CV facts no producer collected.**
  The case-(a) downgrade asked "can *any* non-header backend produce this
  fact", so `TypeField.is_const`/`is_volatile` survived as `PRESENT` on a
  PDB-derived document purely because their registry entry names `dwarf` —
  a producer that document has no trace of, and one whose fresh equivalent
  (`pdb_model._record_from_layout`) states `UNSUPPORTED` outright. The
  decision now asks which producers *this document evidences*
  (`storage/fact_backfill.evidenced_producers`): recorded — never inferred
  — header provenance for `castxml`/`clang`, a real DWARF block for
  `dwarf`, the document's own `platform` for `elf`/`pe`/`macho`. `pdb`,
  `btf` and `ctf` are deliberately never inferred, since no document field
  distinguishes them; a fact naming only those would fail closed rather
  than silently claim itself.
