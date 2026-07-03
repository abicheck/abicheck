# case161_target_dependency_added — New inter-target build/link dependency

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Finding:** `target_dependency_added` · **Evidence tier:** L5

> These cases ship a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json`) instead of compiled `v1`/`v2` binaries, so the corpus is validated
> compiler-free by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../scripts/gen_l3l4l5_examples.py).

## What it demonstrates

The L5 build graph gains a `TARGET_DEPENDS_ON` edge from `libdemo` to `libcrypto` between v1 and v2. The shipped artifact may now require an additional library at load time and takes on that dependency's ABI transitively.

## Why no single artifact layer sees it

| Source | What it sees alone |
|--------|--------------------|
| Binary (L0/L1) | the exported symbol looks unchanged |
| Header AST (L2) | the public declaration looks unchanged |
| **Source graph (L5)** | the derived reachability/ownership delta → the finding |

The build-graph edge is L3/L5 structure, not something a single artifact's contents reveal until you inspect its `DT_NEEDED`. Surfacing it from the graph lets abicheck flag the coupling and localize which target introduced it.

Per ADR-028 D3 a build/source-evidence finding never decides a shipped-ABI break
on its own — the artifact diff proves any concrete break; this finding flags the
elevated risk (or source/API break) and localizes the cause for review.

## How to fix

Confirm the new dependency is intended and ships with the library; if it is an implementation detail, hide it (static-link it, or `-fvisibility=hidden`) so it does not leak into consumers' deployment requirements.
