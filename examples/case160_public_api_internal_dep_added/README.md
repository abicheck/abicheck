# case160_public_api_internal_dep_added — Public API newly depends on an internal declaration

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `public_api_internal_dependency_added` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

In the derived L5 source graph the exported entry `demo::parse` newly calls the internal (non-public-header) helper `detail::validate` that it did not reach in v1. The public surface has taken on an undeclared dependency, so a later change to the internal entity becomes a hidden behavioral risk to the API.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | the exported symbol looks unchanged |
| Header AST (L2) | the public declaration looks unchanged |
| **Source graph (L5)** | the derived reachability/ownership delta → the finding |

The call target is internal — it has no public declaration and (being inlined/internal) may leave no distinct exported symbol — so an artifact diff shows the public symbol unchanged. Only the L5 call graph reveals the new reachability. It is the version-over-version analogue of the intra-version `public_to_internal_dependency` cross-check.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Either promote the internal entity to a documented part of the API, or keep the public entry's behavior independent of internals whose evolution consumers cannot track.
