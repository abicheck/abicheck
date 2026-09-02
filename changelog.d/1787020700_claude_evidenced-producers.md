### Fixed

- **A legacy snapshot could claim facts no producer ever collected.** The
  case-(a) fact downgrade applied on load asked "can *any* non-header
  backend produce this fact" — so `TypeField.is_const`/`is_volatile`
  survived as `PRESENT` on a PDB-derived document purely because their
  registry entry names `dwarf`, a producer that document has no trace of
  and whose fresh equivalent (`pdb_model._record_from_layout`) reports
  `UNSUPPORTED` outright.

  The decision now asks which producers *this document evidences*
  (`storage/fact_backfill.evidenced_producers`): `castxml`/`clang` from
  **recorded** — never inferred — header provenance, and `elf`/`pe`/`macho`
  from the document's own `platform`. Nothing else. In particular **a debug
  block evidences no producer at all**: `dwarf`/`dwarf_advanced` are debug
  *storage*, not a format claim, and a placeholder written for a binary
  with no debug info, a real DWARF parse, a BTF or CTF conversion
  (`BtfMetadata.to_dwarf_metadata`) and a parsed PDB
  (`pdb_metadata.parse_pdb_debug_info`) are all indistinguishable inside a
  stored document — `dwarf_advanced.has_dwarf` included, which
  `dwarf_presence._section_presence_metadata` also sets for BTF and CTF.
  So `dwarf`, `pdb`, `btf` and `ctf` are never inferred; a fact left with
  no evidenced producer fails closed to `NOT_COLLECTED` rather than
  silently claiming itself.

  Four rules are affected — `TypeField.is_const`/`is_volatile` and
  `RecordType.vtable`/`vptr_offset_bits`, the ones naming `dwarf` — and only
  on a legacy **non-header** document still holding the field's resting
  default. The claim narrows; the value does not: a record with a real
  vtable keeps it and stays `PRESENT`. Recorded-header and freshly written
  snapshots are unaffected (the latter persist each `<field>_fact` directly
  and never reach this correction).

- `apply_case_a_fact_backfill`/`apply_legacy_fact_backfill` now require
  their `evidenced` argument instead of defaulting it to both header-AST
  backends — the most permissive value there is, so an omitted argument
  kept a header-only fact `PRESENT` on a document with no header provenance
  (`storage/AGENTS.md`: never let a default value stand in for missing
  evidence).

- A falsy `<field>_fact` payload in a legacy document — `{}`, `null`, or any
  other empty value — no longer bypasses that gate. The backfill skipped an
  entry whose `<field>_fact` *key* was present, but `decode_fact` treats a
  falsy payload as no fact at all, so the owning dataclass's bridge derived
  `PRESENT` from the legacy value and the producer gate never ran. A
  truncated or hand-authored document could therefore reach exactly the
  confirmed claim this mechanism exists to prevent. The skip now tests for a
  usable fact, the same falsy check the decoder applies.
