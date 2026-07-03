# case162_symbol_source_owner_changed — Exported symbol's owning source file moved

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `exported_symbol_source_owner_changed` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

The exported symbol `demo::init` is produced by `src/init_legacy.cpp` in v1 and by `src/init.cpp` in v2 (per the L5 source graph), while its name and signature are unchanged. The implementation behind a stable public symbol relocated across translation units.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | the exported symbol looks unchanged |
| Header AST (L2) | the public declaration looks unchanged |
| **Source graph (L5)** | the derived reachability/ownership delta → the finding |

The symbol name and signature are identical, so the artifact diff is quiet. Only the L5 source→symbol ownership mapping shows the implementation moved — a refactor that can change inlining, static-initialization order, or introduce an ODR risk if the old location still defines it.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Confirm the relocation is intentional and that no other TU still defines the symbol; review for static-init-order and inlining effects on consumers.
