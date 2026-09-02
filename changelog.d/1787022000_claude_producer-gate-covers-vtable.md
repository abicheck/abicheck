### Fixed

- The legacy-load producer gate's documented reach was understated: four
  rules in the backfill table name `dwarf`, not two — `TypeField.is_const`/
  `is_volatile` and `RecordType.vtable`/`vptr_offset_bits`. A pre-fact-field
  snapshot's resting `vtable: []` on a non-header document now resolves
  `NOT_COLLECTED` rather than `PRESENT`, the same answer it already got for
  a PE/PDB or symbols-only document. It cannot be narrower: keeping the
  DWARF case would mean reading "ELF plus a populated debug block" as DWARF,
  which a BTF or CTF snapshot satisfies identically while carrying no vtable
  information at all.
