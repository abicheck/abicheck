# case158_public_typedef_removed — Public typedef removed

**Verdict:** 🟠 API_BREAK · **Finding:** `public_typedef_removed` · **Evidence tier:** L4

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

The public typedef `demo::handle_t` is present in v1 and removed in v2. Consumer source that named the alias (variables, casts, template arguments) no longer compiles.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | nothing — the construct leaves no artifact footprint |
| Header AST (L2) | partial — but source replay is what records this surface authoritatively |
| **Source replay (L4)** | the per-TU source surface → the finding |

A bare typedef emits no symbol of its own, so the artifact diff is blind to its removal. Source replay surfaces it. (The sibling `public_typedef_target_changed` covers the case where the alias is kept but re-pointed.)

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Retain the alias, or provide a replacement name and update consumers before removing the old one.
