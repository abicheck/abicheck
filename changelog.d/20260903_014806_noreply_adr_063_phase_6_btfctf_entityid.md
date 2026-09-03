### Added

- **BTF/CTF-sourced struct/enum types now get a real `entity_id` and
  populate `AbiSnapshot.semantic_ir`** — `extract/debug_layout_semantic_ir.py`
  bridges the shared `DwarfMetadata` shape both
  `btf_metadata.parse_btf_metadata` and `ctf_metadata.parse_ctf_metadata`
  reduce to into transient, `entity_id`-bearing `RecordType`/`EnumType`
  objects. Unlike PDB and DWARF, BTF/CTF need no scope-resolution heuristic
  at all — both are pure-C debug formats with no namespace/class nesting,
  so every name gets an unconditionally empty `ScopePath`. Wired into the
  existing ELF headerless (symbol-only) fallback path. Deliberately does
  not change `AbiSnapshot.types`/`.enums` for a BTF/CTF-sourced snapshot
  (left empty, as before) — only `semantic_ir` gains occurrences; widening
  what other `.types`-consuming detectors see is a separate, larger design
  question this slice does not attempt. Function/variable/typedef identity
  remains unimplemented (neither format's own richer parse carries that
  evidence across its own `to_dwarf_metadata()` conversion at all).
  `snapshot_cache.py`'s `_SNAPSHOT_CACHE_VERSION` is bumped so a snapshot
  cached by an older abicheck build doesn't keep serving `semantic_ir=None`
  forever for an auto-detected BTF/CTF dump's identical cache-key inputs.

### Fixed

- **A `symbols_only`/`debug_presence_only` dump with an explicit BTF/CTF
  `debug_format` no longer stamps a falsely-empty `SemanticIR`.** Both
  modes resolve debug metadata through the cheap
  `dwarf_presence.cheap_debug_presence_metadata` probe, which confirms
  only that BTF/CTF debug info *exists* (`has_dwarf=True`) without ever
  parsing its structs/enums. `_dump_elf` was passing the resolved format
  straight through regardless, so `_build_symbol_only_snapshot`'s BTF/CTF
  branch built `SemanticIR(occurrences={})` from a probe that never
  looked — the same "confirmed empty" vs. "not evaluated" distinction
  this codebase's own `Fact`/`FactStatus` discipline exists to preserve
  elsewhere. `semantic_ir` is now left `None` on either presence-only path
  (Codex review, fresh evidence).
- **A bare BTF/CTF blob (no ELF container) now also populates
  `semantic_ir`** (Codex review, fresh evidence): `workflows/
  input_resolution.py::_resolve_raw_typeinfo` is a second, independent
  production call site that constructs the `AbiSnapshot` directly from a
  raw `.btf`/`.ctf` file and never goes through `dumper_elf_fallback.py`'s
  own fallback, so it kept stamping `semantic_ir=None` despite carrying
  the identical struct/enum metadata. Now calls the same
  `semantic_ir_from_debug_metadata` this slice already uses elsewhere.

### Testing

- Added `tests/test_dumper_elf_fallback_coverage.py`, exercising
  `dumper_elf_fallback._try_dwarf_snapshot`'s three previously-untested
  branches (the `--dwarf-only`-with-headers warning, the "headers were
  actually given" suppression of the no-headers info log, and the
  "DWARF produced no functions/variables of its own" types-only fallback)
  by monkeypatching `dwarf_snapshot.build_snapshot_from_dwarf` — the same
  pattern `tests/test_dumper_layout_backfill.py` already uses for that
  module — closing the `codecov/patch` gap the Codecov patch-coverage
  check flagged on this PR (`dumper_elf_fallback.py` patch coverage;
  `extract/debug_layout_semantic_ir.py` was already fully covered by the
  existing test suite once run alongside `tests/test_dwarf_semantic_ir.py`).

### Documentation

- **`extract/debug_layout_semantic_ir.py` documents an inherited, accepted
  limitation** (Codex review, fresh evidence): a same-named duplicate
  struct/enum collapses to one `SemanticIR` occurrence, not two, because
  `DwarfMetadata.structs`/`.enums` are already name-keyed, first-wins dicts
  by the time this module receives them — the collapse happens in
  `btf_metadata`'s/`ctf_metadata`'s own raw parse (`_extract_structs`/
  `_extract_enums`), well before `to_dwarf_metadata()` runs, and every
  pre-existing `_diff_dwarf`/`_diff_advanced_dwarf` detector already lives
  with the identical collapse. A real fix needs both raw parsers to carry
  multiple same-named records forward with a genuine per-record
  discriminator — a materially larger, separately-scoped project touching
  the shared `DwarfMetadata` contract every other BTF/CTF-backed consumer
  depends on, not a heuristic this types-only slice can safely improvise.
