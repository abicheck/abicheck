# case153_struct_packing_flip — Struct-packing mode flip (`-fpack-struct`)

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `struct_packing_mode_changed` · **Evidence tier:** L3

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

v1 uses natural alignment, v2 adds `-fpack-struct=1`. Reduced packing removes inter-member padding, so every member offset and the type's `sizeof` can change with no source or symbol change. Consumers compiled against the old packing read fields at stale offsets.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | one self-consistent build — the flag is not in the artifact |
| Header AST (L2) | the declarations, but not the flags the library was built with |
| **Build context (L3)** | the captured compile options — the flag flip → the finding |

Nothing in the exported symbol table records the packing policy; a single build looks internally consistent. Only the L3 build flag (or the artifact/type diff of two builds) exposes the mismatch.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Use one packing policy across the library and its consumers. Prefer explicit `#pragma pack` / `alignas` on the specific types that need it over a global `-fpack-struct`.
