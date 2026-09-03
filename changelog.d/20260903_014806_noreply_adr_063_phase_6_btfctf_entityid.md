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
