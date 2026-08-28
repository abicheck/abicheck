### Changed

- **`abicheck.model` is now a package, and the binary/debug fact dataclasses
  moved into it from their parsers (ADR-061 Phase 5).** The flat
  `abicheck/model.py` became `abicheck/model/`, split by responsibility into
  `vocabulary`, `declarations`, `entities`, `extraction_contract`, `snapshot`
  and `stdlib_surface`; every name the flat module exported still resolves
  from `abicheck.model`, and `__all__` pins that surface. Alongside it, eleven
  modules that conflated a model dataclass with the code that fills it in —
  `elf_metadata`, `pe_metadata`, `macho_metadata`, `dwarf_metadata`,
  `dwarf_advanced`, `sycl_metadata`, `symvers_metadata`, `python_api`,
  `python_ext`, `numpy_capi` and `build_mode` — kept their parsing and handed
  their dataclasses to `model/*_facts.py`. Each parser imports and re-exports
  its own types, so `from abicheck.elf_metadata import ElfMetadata` and every
  sibling spelling are unchanged. This is what unblocks `service.py`'s own
  migration: `AbiSnapshot` no longer needs an extractor to describe its own
  field types.
- **`AbiSnapshot.index()` builds its three lookup maps through one primitive
  instead of three copies of the same loop.** `model/first_wins_index.py`
  states the shared rule once — the first declaration to claim a key keeps it,
  and later claimants are counted so the caller can report what it dropped —
  with its contract covered by property tests rather than only through the one
  caller. Behaviour is unchanged, including the wording and the ×N counts of
  the duplicate-symbol warnings.
