# Graph Coverage & Negative Evidence

The optional embedded [L5 source graph](build-source-data.md) can prove a
*positive*: "public entry X reaches internal declaration Y" (a
`DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`/... edge exists). It is much harder
to trust the graph for a *negative*: "no public entry reaches Y" is only
true if the collection pass that built the graph actually looked everywhere
it needed to. This page explains why absence of an edge is not always proof
of absence of a dependency, and how abicheck's suppression gate reflects
that honestly instead of guessing.

## Why an absent edge isn't automatically proof

`SourceGraphSummary` (the in-memory L5 graph abicheck's snapshot carries)
records, per extractor pass, whether its own coverage was complete:

- `extractor_passes` — the pass ran over the **full** project scope with no
  errors. An edge family with a `extractor_passes` entry is trustworthy for
  both "this edge exists" and "this edge does not exist".
- `narrowed_passes` — the pass ran, but only over a **restricted** scope
  (e.g. a `--changed-paths`-scoped run). An edge found there is still real;
  an edge *not* found there proves nothing about the parts of the project
  the pass never looked at.
- `degraded_passes` — the pass hit collection errors (a translation unit
  failed to parse, a tool crashed) but still folded in whatever edges it
  managed to extract before failing. The edges it *did* find are real; the
  ones it didn't are an unknown, untracked gap — not evidence of absence.

Two collection strategies commonly produce exactly this shape:

- **Header-only collection** (`--header-graph`, the implicit no-real-build
  path) sees declarations and signatures but never a function body, so it
  cannot see a `DECL_CALLS_DECL` edge a public inline function's *body*
  creates into an internal specialization — the graph is real, just
  structurally unable to answer that question.
- **A collector-upgrade** (old snapshot dumped with `--header-graph`, new
  snapshot with a real `--build-info` compile database) is not a "new
  dependency appeared" signal — it is the same project seen through two
  different lenses. abicheck's [source-graph
  diff](build-source-data.md) findings account for this asymmetry rather
  than reporting phantom `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` churn every
  time collection tooling improves.

## Tri-state reachability

Because of this, `Change.reachability_state` is not the boolean
`Change.public_reachable` alone — it is one of three states:

| State | Meaning |
|---|---|
| `reachable` (`PROVEN_REACHABLE`) | A walk positively found a path from the public surface to this change. |
| `unreachable` (`PROVEN_UNREACHABLE`) | A walk examined this change and found no path — and the walk's own coverage was trustworthy for that verdict (the type-layout walk always is; the call-graph walk is, as long as it wasn't the *only* signal available while flagged narrowed/degraded). |
| `unknown` | No walk reached a verdict at all, or the only walk that could have was itself narrowed/degraded coverage — the honest "we don't know" answer. |

`MarkReachability` (the pipeline step that computes this, before suppression
runs) sets this alongside the existing `public_reachable` boolean — see
[ADR-044](../development/adr/044-reachability-aware-suppression.md) for the
boolean's original design.

## What this means for suppression

The suppression `reachability: unreachable-only` default (the common case
for a broad `namespace`/`source_location` rule) keeps its original,
boolean-only semantics for backward compatibility: it treats `unreachable`
and `unknown` identically, exactly as it always has. That is deliberately
unchanged — most projects have no embedded L5 graph at all, and the
type-layout walk (which has no coverage caveat) already dominates that
common case.

For a project that *does* rely on L5 graph evidence and wants a suppression
rule to require actual proof, opt into the stricter gate with
`reachability: proven-unreachable-only` — see [Suppressions § Proven vs.
unknown reachability](../user-guide/suppressions.md#proven-vs-unknown-reachability)
for the rule syntax and the `suppression_reachability_unknown` diagnostic it
produces when coverage isn't good enough to prove a match.
