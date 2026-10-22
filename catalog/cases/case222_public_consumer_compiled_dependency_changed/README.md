# Case 222: Public Inline Body's Compiled-In Dependency Changes

**Category:** Build/Source Evidence (L2) | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> This case ships a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json` — `SourceGraphSummary` dumps of the header-only graph) instead of
> compilable `v1`/`v2` sources, so the corpus is also validated compiler-free
> by `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_consumer_cases.py`](../../../scripts/gen_l3l4l5_consumer_cases.py)
> for how they were generated.

## Verdict and consumer impact

The public inline function `demo::scale(int)` is written in the public header
`include/demo/scale.h`. Its body is therefore compiled into every consumer
translation unit that includes the header. Nothing is exported and no symbol
changes. In v2 the body also calls `demo::detail::saturate()`, a new private
dependency. A consumer must recompile to pick up the new body, so the change
is a risk to review, not a compatibility break.

## Old/new diff

| v1 header graph | v2 header graph |
|---|---|
| `demo::scale(int)` declared by `include/demo/scale.h`; its header-written body calls `demo::detail::mul` | same declaration and call; body now also calls `demo::detail::saturate` |
| `demo::detail::mul` is a private-header declaration | unchanged |
| — | new: `demo::detail::saturate` (private header) and a `DECL_CALLS_DECL` edge from `demo::scale` to it |

The detector compares the dependency set of each public declaration whose body
the header call pass parsed in full, and only when that pass ran to completion
on both sides. A body that calls the same dependencies in a different order
produces no finding, and a side whose header pass was narrowed or degraded
produces no finding either.

## Why this is a risk, not a break

Without consumer build evidence the graph cannot say which consumers include
the header or which of them compiled the changed body. The finding therefore
stays `COMPATIBLE_WITH_RISK`. It never upgrades to `BREAKING` on header-graph
evidence alone, and it never reads as "no consumer uses this dependency".

## What the detector does not claim

It makes no clean-compatibility claim when the evidence is incomplete. A side
whose header call pass was narrowed, degraded or absent produces no finding,
and that silence means the body's dependencies were not established, not that
they are unchanged. It also does not infer consumer usage from any symbol
name. Macro-compiled dependencies and the consumer-side build evidence that
would promote the finding to a proven break are not covered by this tier.
