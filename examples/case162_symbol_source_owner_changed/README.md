# case162_symbol_source_owner_changed — Exported symbol's declaring file moved

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `exported_symbol_source_owner_changed` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

The exported symbol `demo::init` is declared by `include/demo/legacy.h` in v1 and by `include/demo/init.h` in v2 (per the L5 source graph's `SOURCE_DECLARES` edge), while its name and signature are unchanged. The file that owns the declaration behind a stable public symbol moved.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | the exported symbol looks unchanged |
| Header AST (L2) | the public declaration looks unchanged |
| **Source graph (L5)** | the derived reachability/ownership delta → the finding |

The symbol name and signature are identical, so the artifact diff is quiet. Only the L5 declaration→symbol ownership mapping shows the declaring file moved — a refactor that can change consumers' include paths, inlining, or introduce an ODR risk if the old location still declares it.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Confirm the declaration move is intentional and that no other file still declares the symbol; review include-path and inlining effects on consumers.
