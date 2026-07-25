<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`dump()` now populates `AbiSnapshot.contract`** (ADR-050 D1 Phase 1):
  every real `dump()` call that runs the header (L2) frontend now calls
  `comparability.compute_extraction_contract(...)`, stamping
  `compiler_family`, `compiler_version`, `abi_dialect`, ordered
  `macro_ops`/`pass_through_flags`, `declared_headers`/`declared_includes`,
  and public-header scope into `AbiSnapshot.contract`. This makes the
  `check_contracts_comparable` gate already wired into `checker.compare()`
  live for the first time — previously every snapshot had `contract=None`,
  so the gate was a permanent no-op. The contract is correctly left `None`
  for `symbols_only`/`dwarf_only`/no-headers dumps (gated on
  `AbiSnapshot.from_headers`, not on whether a `headers` argument was
  merely supplied, since `dwarf_only=True` accepts but ignores it).
  `depfile_resolved_paths`/`generated_driver_path`, `target_triple`,
  `pointer_width`, and `endianness` remain unset — deferred follow-up work,
  not silently dropped (documented in `comparability.py`'s module
  docstring).
- `header_conditionals.ordered_macro_ops()` and
  `pass_through_flags_from_tokens()`: new pure functions extracting an
  order- and value-preserving macro-op stream and `-include <path>`
  pass-through flags from a compiler-flag token stream, feeding the new
  `dump()` wiring above.
- `dumper_toolchain._compiler_family_from_toolchain()`: best-effort
  `compiler_family` label (`clang`/`msvc`/`gnu`/raw basename) derived from
  the resolved host-compiler binary recorded in `AbiSnapshot.ast_toolchain`.
- `AbiSnapshot.ast_toolchain` now also carries an `abi_dialect` key
  (`gnu`/`msvc`) for both the castxml and clang header-AST backends.

### Changed

- Relocated the ELF-visibility/symbol-classification helpers
  (`_ELF_VIS_MAP`, `_populate_elf_visibility`, `_elf_classify_symbols`) from
  `dumper.py` to a new sibling module `abicheck/dumper_elf_symbols.py`, with
  a re-export shim preserving all existing bare-name call sites and test
  patch targets — a pure relocation freeing line budget in `dumper.py`
  (which sits at the AI-readiness file-size cap) for the wiring above.
