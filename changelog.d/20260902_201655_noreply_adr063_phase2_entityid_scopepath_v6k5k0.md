### Added

- **`entity_id` now reaches the flat detector modules where a matched declaration is available** — `diff_platform.py`'s ELF-fallback deleted-function finding and `diff_platform_elf_symbols.py`'s exported-object size/alignment findings now carry `Change.entity_id` (ADR-063 Phase 2). The remaining flat detectors (`diff_platform_elf_dynamic.py`, `diff_versioning.py`, `diff_sycl.py`, most of `diff_platform.py`) operate on raw ELF/PE/Mach-O container facts or DWARF-only layout structs that don't carry `entity_id` at all, so they are unaffected.
