# Case 219: Public Virtual Method's Override Set Changes

**Category:** Build/Source Evidence (L5) | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

> This case ships a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json` — `SourceGraphSummary` dumps) instead of compilable `v1`/`v2`
> sources, so the corpus is also validated compiler-free by
> `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../../scripts/gen_l3l4l5_examples.py)
> for how they were generated.

## Verdict and consumer impact

The exported virtual method `demo::Shape::area()` keeps its symbol, signature
and call site. In v2 a second override, `demo::Square::area()`, joins
`demo::Circle::area()` among the implementations a virtual call through
`Shape::area()` may dispatch to. Nothing about the shipped ABI changes, so
this is not a compatibility break. It is a risk: the set of possible runtime
targets behind a stable public virtual changed, and the new target can alter
behavior for code that calls through the base without any symbol or
signature changing. The set is an approximate, over-approximated join of the
source graph, so the finding never upgrades to a break on its own.

## Old/new diff

| v1 source graph | v2 source graph |
|---|---|
| `demo::Shape::area()` declared by `include/demo/shape.h`, a virtual call from `demo::render()` dispatches to `{Circle::area}` | same call and declaration; dispatch set now `{Circle::area, Square::area}` |
| `demo::Circle::area()` overrides `Shape::area()` | unchanged |
| — | new: `demo::Square::area()` overrides `Shape::area()`, and a `VIRTUAL_CALL_MAY_DISPATCH_TO` edge joins it to the call |

The detector compares the candidate set per public virtual base as a set, and
only when the call, override and virtual-dispatch passes ran to completion on
both sides. A reordered set, or a side whose passes were narrowed or degraded,
produces no finding (see case 220 for the reordered twin).

## Why this is a risk, not a break

The override set is an over-approximation of a runtime decision: the source graph lists every override a virtual call through `Shape::area()` *may* reach, not the one a given call takes. A changed set therefore says the possible behavior behind a stable symbol moved. It does not prove that any consumer's call now lands in different code, so the finding is `COMPATIBLE_WITH_RISK` and never gates the exit code on its own.

## What the detector does not claim

It makes no clean-compatibility claim when the evidence is incomplete. A side whose call, override or virtual-dispatch pass was narrowed or degraded produces no finding, and that silence means the set was not established, not that it is unchanged.
