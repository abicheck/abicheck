### Fixed

- **A dropped *weak* export is no longer reported as a removal** — the export
  table alone cannot tell a removed strong definition from a removed COMDAT
  copy the consumer already carries, so abicheck called both `BREAKING`. A
  `WEAK` binding on a C++ entity is what the compiler emits for something the
  language requires *every* using translation unit to define for itself — an
  inline function, a template instantiation, an implicit special member — so
  when such an export disappears while the new headers still define the entity
  inline, a consumer built against those headers keeps resolving against its
  own copy. That case is now `func_export_dropped_inline_available`
  (`COMPATIBLE_WITH_RISK`) instead of `func_removed`/`func_deleted_elf_fallback`
  (both `BREAKING`, on the same symbol).

  The demotion deliberately does not rest on "nobody uses it", which two
  snapshots cannot show; it rests on "the header still defines it, so a user
  emits its own copy", for which the new side's own inline declaration is
  direct evidence. It stays a *risk* rather than silence because a consumer
  built against a header that only declared the entity, or one comparing its
  address across the library boundary expecting a single shared instance, can
  still be affected.

### Added

- **Snapshots record ELF symbol binding** (`Function.elf_binding` /
  `Variable.elf_binding`, schema v21) — read from the same `.dynsym` entry as
  the existing `elf_visibility`. Tri-state: `None` means "not captured"
  (non-ELF platform, header-only declaration, pre-v21 snapshot) and is never
  read as a strong or weak binding, so the demotion above stays inert wherever
  the evidence is absent rather than acting on a guess. `STB_GNU_UNIQUE` is
  kept distinct from `WEAK` rather than folded into it: it is a strong,
  process-wide-unique definition, and the GNU extension exists precisely to
  stop each consumer using its own copy.
