# libabigail Parity Matrix

_G3: libabigail test suite compatibility_

This document tracks how abicheck verdicts compare to `abidiff` (libabigail)
on canonical ABI change scenarios. It is a development/QA tracking page — if
you are switching from `abidiff` to abicheck, see
[Migrating from libabigail](../user-guide/from-libabigail.md) instead.

**Source of truth:** `PARITY_CASES` in `tests/test_abidiff_parity.py`. Each
case carries a status — `parity` (both tools agree), `correct` (abicheck is
authoritative; abidiff is conservative), or `divergence` (intentional, stable
divergence). The tables below mirror that table; update them together.

## Confirmed parity (both tools agree)

| # | Case | Change | abicheck | abidiff |
|---|------|--------|----------|---------|
| 1 | fn_removed | Function removed from dynsym | BREAKING | BREAKING |
| 2 | fn_added | New function added | COMPATIBLE | COMPATIBLE |
| 3 | no_change | Identical libraries (ELF-only, no headers) | NO_CHANGE | NO_CHANGE |
| 4 | visibility_hidden | Public → hidden visibility | BREAKING | BREAKING |
| 5 | vtable_reorder | C++ vtable method order swap | BREAKING | BREAKING |

The historical `vtable_reorder` gap (abicheck ELF-only missed it) is **closed**
— with headers (castxml) both tools report BREAKING.

## abicheck correct, abidiff conservative (G3 closed)

Without `--headers-dir`, abidiff classifies these as sub-type drift
(`COMPATIBLE`, exit 4); abicheck with headers sees the actual signature
change:

| # | Case | Change | abicheck | abidiff (no headers-dir) |
|---|------|--------|----------|--------------------------|
| 1 | return_type | `int get_val()` → `long get_val()` | BREAKING | COMPATIBLE |
| 2 | param_type | `set_val(int)` → `set_val(long)` | BREAKING | COMPATIBLE |

## Intentional divergences (stable)

| # | Case | Change | abicheck | abidiff | Rationale |
|---|------|--------|----------|---------|-----------|
| 1 | struct_size | Field added to returned-by-value struct | BREAKING | COMPATIBLE¹ | abicheck is correct; abidiff without `--headers-dir` sees only compatible sub-type drift |
| 2 | enum_value | Enum member value changed | BREAKING | COMPATIBLE | abicheck is intentionally stricter — enum value changes break switch/serialization |

¹ abidiff with DWARF but no headers classifies type sub-changes as COMPATIBLE
(exit=4), not BREAKING; with `--headers-dir` it strengthens. abicheck with
headers (castxml) returns BREAKING either way.

## Maintaining this page

When a case's behaviour changes, update its status in `PARITY_CASES`
(`tests/test_abidiff_parity.py`) — the parametrized tests over the derived
`_CONFIRMED` / `_CORRECT` / `_DIVERGE` views fail with a "move this case"
message when reality and status disagree — then mirror the change here.

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
