# case152_enum_size_flag_flip — Enum-size flag flip (`-fshort-enums`)

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `enum_size_flag_changed` · **Evidence tier:** L3

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

v1 builds with default (int-sized) enums, v2 adds `-fshort-enums`. The two builds have identical source and identical exported symbols; only the captured build flag differs. `-fshort-enums` makes the compiler pick the smallest integer type that holds an enum's range, so an enum member of a public struct, an enum-typed parameter, or an enum return value changes size — and as a struct member it shifts every field after it.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | one self-consistent build — the flag is not in the artifact |
| Header AST (L2) | the declarations, but not the flags the library was built with |
| **Build context (L3)** | the captured compile options — the flag flip → the finding |

A symbol-only (L0) or even DWARF-only (L1) check of *one* build sees a self-consistent binary; the incompatibility only exists *between* two builds made under different enum-size assumptions, which lives in the L3 build options.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Build the library and all its consumers with the same `-fshort-enums` setting, or avoid exposing bare enums in the ABI (use fixed-width underlying types: `enum E : int { … }`).
