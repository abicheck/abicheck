### Changed

- **Struct and enum layout diffing moved out of `diff_platform`** — `diff_struct_layouts` and `diff_enum_layouts` now live in `abicheck/compare/type_layout_diff.py` (the ADR-061 `compare/` owner), and the ELF `_both_captured_elf_identity` gate is the shared `abicheck/compare/elf_capture_identity.py:both_captured_elf_identity`, so the two ELF splits no longer import each other's private. `abicheck.checker` still re-exports `diff_struct_layouts`. Detector order and output are unchanged.
