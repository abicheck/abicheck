# case156_public_macro_removed — Public macro removed

**Verdict:** 🟠 API_BREAK · **Finding:** `public_macro_removed` · **Evidence tier:** L4

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

The public header macro `DEMO_MAX_ITEMS` is present in the v1 source-replay surface and gone from v2. Source that referenced the macro (a constant, a feature guard, a function-like macro) no longer compiles.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | nothing — the construct leaves no artifact footprint |
| Header AST (L2) | partial — but source replay is what records this surface authoritatively |
| **Source replay (L4)** | the per-TU source surface → the finding |

Macros are a preprocessor construct — they never reach the binary, so no artifact layer (L0/L1/L2 debug/symbols) can see the removal. Only the L4 per-TU source-replay surface records public macros.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Keep a compatible macro (optionally `#define`-forwarding to a replacement), or document the removal and provide a migration path for consumers.
