# case157_inline_function_removed — Public inline function removed

**Verdict:** 🟠 API_BREAK · **Finding:** `inline_function_removed` · **Evidence tier:** L4

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

The public header-only inline function `demo::clamp` is present in v1 and removed in v2. Source that called the inline no longer compiles.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | nothing — the construct leaves no artifact footprint |
| Header AST (L2) | partial — but source replay is what records this surface authoritatively |
| **Source replay (L4)** | the per-TU source surface → the finding |

Because it was `inline`, the function had no exported binary symbol, so the artifact diff (L0) sees nothing. Only the L4 source-replay surface observes the lost declaration.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Keep a compatible declaration (it can forward to a replacement), or move the removal behind a documented deprecation window.
