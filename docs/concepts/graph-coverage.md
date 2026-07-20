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

- **Header-only collection** (the L2 header-only graph, attached
  automatically whenever a supported `dump`/`compare` run has header
  evidence at `--depth headers` or deeper — including runs that *also*
  provide real build/source evidence, not just header-only ones) sees
  declarations and signatures but never a function body, so it cannot see a
  `DECL_CALLS_DECL` edge a public inline function's *body* creates into an
  internal specialization — the graph is real, just structurally unable to
  answer that question.
- **A collector-upgrade** (old snapshot dumped header-only, new snapshot
  with a real `--build-info` compile database) is not a "new
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

## Migration: header-graph is now default-on

Before G29 Phase A, the L2 header-only graph (and its
`COMPILE_UNIT_INCLUDES_FILE` include-file extension) only got built if you
explicitly passed `--header-graph`/`--header-graph-includes` to `dump` or
`compare`. As of G29 Phase A, `--depth headers` (the default depth) always
builds it automatically — there is no flag to remember and nothing to opt
into. The two flags still exist but are hidden, deprecated no-ops kept only
for a transition window before removal.

This doesn't change how you should reason about completeness: whether the
graph saw everything it needed to is still reported through the coverage
fields described above (`extractor_passes`/`degraded_passes`/
`narrowed_passes` and the tri-state `reachability` status), never through
whether a flag was passed. A header-only collection degrades the same way
it always did (declarations and signatures only, no function bodies) — it
is just no longer possible to accidentally run *without* it when depth
`headers` or deeper evidence is available.

## Canonical entity identity and rename/move reconciliation (G31 Phase B)

The header-only graph and a build-integrated graph can identify the same
declaration differently depending on which pass saw it first. Without any
reconciliation, an old/new comparison sees a renamed internal declaration as
an unrelated node removal plus an unrelated node addition — a reader has to
notice the two facts independently and infer by hand that they describe the
same entity.

`abicheck.buildsource.entity_identity` computes a **canonical identity** for
every graph declaration/type node, in preference order:

1. **canonical** — a compiler-provided stable identity (a clang USR, when a
   producer supplies one) or a real Itanium/MSVC mangled name.
2. **normalized** — a fully-qualified semantic signature (qualified name +
   kind + arity/parameter types) when no mangling is available.
3. **reduced** — a source-relative identity (file + enclosing scope + name,
   always an alias, never the primary key) or, when nothing else is
   available at all, a clearly-marked `synthetic:sha256:...` fallback.

`abicheck.buildsource.graph_reconcile` then reconciles an old/new graph
diff's added/removed nodes using that identity: an exact canonical-id match,
an exact (bidirectionally-unambiguous) alias match, or — as a last resort —
a match on unique structural position when even the qualified name changed.
**Ambiguous evidence never resolves to a guess**: if two candidates share
the same alias or structural position, neither is reconciled — both stay a
plain add/remove, exactly as before Phase B. A match produces a
`declaration_renamed`, `declaration_moved`, or
`declaration_identity_reconciled` finding — pure enrichment, RISK-tier,
never overriding or suppressing an artifact-proven finding elsewhere in the
comparison (the same authority rule as everywhere else on this page).

See [ADR-048](../development/adr/048-canonical-entity-identity-and-graph-reconciliation.md)
for the full design, and
[`examples/case194_header_graph_rename_reconciled`](../examples/case194_header_graph_rename_reconciled.md)/
[`examples/case195_header_graph_ambiguous_rename_not_reconciled`](../examples/case195_header_graph_ambiguous_rename_not_reconciled.md)
for a reconciled rename and its deliberately-unreconciled ambiguous
counterpart.
