# libabigail Parity Matrix

_G3: libabigail test suite compatibility_

This document tracks how abicheck verdict compares to `abidiff` (libabigail)
on canonical ABI change scenarios. It is a development/QA tracking page — if
you are switching from `abidiff` to abicheck, see
[Migrating from libabigail](../user-guide/from-libabigail.md) instead.

## Confirmed Parity (both tools agree)

| # | Case | Change | abicheck | abidiff |
|---|------|--------|----------|---------|
| 1 | fn_removed | Function removed from dynsym | BREAKING | BREAKING |
| 2 | fn_added | New function added | COMPATIBLE | COMPATIBLE |
| 3 | no_change | Identical libraries | NO_CHANGE | NO_CHANGE |
| 4 | visibility_hidden | Public → hidden visibility | BREAKING | BREAKING |
| 5 | vtable_reorder | C++ vtable method order swap | NO_CHANGE | BREAKING* |
| 6 | enum_value | Enum member value changed | BREAKING | COMPATIBLE† |

\* vtable: abidiff detects via DWARF; abicheck ELF-only misses it (gap, needs castxml)
† enum_value: abicheck is intentionally stricter — enum value changes break switch/serialization

## Known Divergences (tracked gaps)

| # | Case | abicheck | abidiff | Root cause |
|---|------|----------|---------|------------|--------|
| 1 | struct_size | NO_CHANGE | COMPATIBLE¹ | ELF-only: no type info without headers |
| 2 | return_type | NO_CHANGE | COMPATIBLE¹ | ELF-only: same symbol name, no type diff |
| 3 | param_type | NO_CHANGE | COMPATIBLE¹ | ELF-only: same symbol name, no type diff |
| 4 | vtable_reorder | NO_CHANGE | BREAKING | ELF-only: vtable not visible in dynsym |

¹ Note: abidiff with DWARF (-g but no headers) classifies type sub-changes as
COMPATIBLE (exit=4), not BREAKING. To get BREAKING verdict from abidiff,
use `--headers-dir` option. abicheck with headers (castxml) returns BREAKING correctly.

## Gap closure plan

After integrating castxml output into parity tests, all divergences close.
When a gap is closed, update `PARITY_CASES` in `tests/test_abidiff_parity.py`
and move the entry from `_DIVERGE` to `_CONFIRMED`.

## How to run

```bash
# Requires: abidiff (libabigail-tools), gcc/g++
pytest tests/test_abidiff_parity.py -v -m libabigail
```

## abidiff exit code mapping

| Exit code bits | Meaning | abicheck verdict |
|----------------|---------|-----------------|
| 0 | No differences | NO_CHANGE |
| 4 (bit 2) | Compatible sub-type changes | COMPATIBLE |
| 8 (bit 3) | Incompatible changes | BREAKING |
| 12 (bits 2+3) | Both compatible + incompatible | BREAKING |
| 1 (bit 0) | Error | ERROR |
