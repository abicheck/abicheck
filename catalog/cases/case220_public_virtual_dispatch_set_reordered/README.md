# Case 220: Public Virtual Method's Override Set Reordered

**Category:** Build/Source Evidence (L5) | **Verdict:** ✅ NO_CHANGE

> This case ships a hand-built pair of evidence-model fixtures (`old.json` +
> `new.json` — `SourceGraphSummary` dumps) instead of compilable `v1`/`v2`
> sources, so the corpus is also validated compiler-free by
> `tests/test_l3l4l5_examples.py`. See
> [`scripts/gen_l3l4l5_examples.py`](../../../scripts/gen_l3l4l5_examples.py)
> for how they were generated.

## Verdict and consumer impact

Both versions dispatch a call through the public virtual `demo::Shape::area()`
to the same two implementations, `demo::Circle::area()` and
`demo::Square::area()`. Only the order in which the graph lists those edges
differs. The possible target set is unchanged, so there is no finding and the
verdict is `NO_CHANGE`. This is the negative twin of case 219: it guards
against treating an edge-order difference between two graph dumps as a change
in dispatch behavior.

## Old/new diff

| v1 source graph | v2 source graph |
|---|---|
| dispatch edges `render → Circle::area`, `render → Square::area` | the same two edges, listed in reverse order |
