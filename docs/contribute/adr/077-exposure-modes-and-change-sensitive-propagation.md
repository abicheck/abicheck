# ADR-077: Exposure Modes and Change-Sensitive Propagation

**Date:** 2026-10-11
**Status:** Proposed — not implemented. Amends
[ADR-046](046-source-graph-identity-v2-and-evidence-merge.md) D5
(`TraversalPolicy`) and [ADR-044](044-reachability-aware-suppression.md)
(effective public ABI). Sequenced after the identity and per-member
relevance gaps listed under "Prerequisites".

## Context

abicheck already has most of the semantic machinery for L2:

- the header AST is normalized into `SemanticIR`;
- an L2 graph is built by default whenever headers are given;
- type edges carry a role (`param`/`return`/`field`/`base`/…);
- inline and template bodies contribute call and reference edges;
- every edge has provenance and identity resolution;
- coverage is three-valued (`present` / `proven_absent` / `unknown`).

What it cannot answer is the question a maintainer actually asks:

> Which public contracts depend on the *property that changed* on this
> entity, and which facts prove it?

Reachability (`public_api → … → T`) answers a weaker question. Whether a
change to `T` matters depends on **how** each path uses `T`:

| Use of `T` | Changes that matter |
|---|---|
| passed or returned by value; embedded by value; base class | size, alignment, layout, triviality (calling convention) |
| pointer/reference to an incomplete `T` | the name and identity only; layout does not matter |
| inline body reads `T::f` | that one field's offset, type and existence |
| constant or enumerator used in a default argument or inline body | its value; consumers must be rebuilt (source/behaviour, not link) |
| `T` named in a constraint or a specialization | whether a call is still accepted and which overload or specialization it selects |
| inline or template body calls `g` | whether `g` exists and is still exported (symbol availability) |

ADR-046 D5 already names five *propagation shapes*: layout,
symbol-availability, source-contract, behavioural and deployment. But
`TraversalPolicy` there is a traversal filter only. Nothing ties a
`ChangeKind` to the shape it travels on, and `TypeEdge.role` records
syntactic position, not the usage mode. The result is an over-approximation
in both directions:

- a layout change to a type that is reachable only through an opaque
  pointer can still count against the verdict (a false positive);
- a role change on an existing edge is invisible to the structural diff
  ([known gap](../known-gaps/source-graph-structural-diff-is-role-blind.md));
- body-dependent edges are declined wholesale, because coverage is one
  blanket `header_inline_bodies` unit
  ([known gap](../known-gaps/header-body-coverage-is-not-per-body.md)), a
  false-clean.

This ADR is not about a graph database or a new physical store. Per the
ADR-063 D5 amendment and `policy/public_surface_closure.py`, derived
answers are computed from the snapshot's current declarations, not read back
from stored graph attributes.

## Decision

### D1. `ExposureMode` on dependency facts

Each dependency fact from a public entry to an entity carries an
`ExposureMode`, a closed `model/` vocabulary that sits alongside the
existing role:

`BY_VALUE`, `BASE`, `OPAQUE_HANDLE` (pointer/reference to a type that is
incomplete on the public surface), `COMPLETE_INDIRECT` (pointer/reference to
a complete type), `FIELD_ACCESS(field)`, `VALUE_USE` (constant/enumerator in
a default argument or inline body), `CONSTRAINT`, `CALL`, `UNKNOWN`.

`UNKNOWN` is a real value. Producers that cannot decide the mode emit it;
they never default to the weakest mode.

### D2. A sensitivity table owned by `policy/`

A single table maps `(ChangeKind family, ExposureMode)` to an affected
contract: `ABI_LAYOUT`, `SYMBOL_LINK`, `SOURCE_COMPILE`, `REBUILD_ONLY`,
`NONE`, or `UNKNOWN`. ADR-046 D5's five propagation shapes become the rows'
contract axis, and each `TraversalPolicy` is *derived* from this table
instead of hand-built per detector. Rules:

- propagation crosses an edge only when the table says the change is
  sensitive to that edge's mode;
- `UNKNOWN` (mode or coverage) never yields `NONE`, so missing evidence
  narrows the conclusion and is reported (`AGENTS.md`, "Weaker evidence
  narrows conclusions");
- the table changes *relevance and attribution*, never the observed
  `Change`. A change judged `NONE` for every public path is recorded and
  disposed with its rule and reason (ADR-067), not dropped.

### D3. Impact answers carry their proof

The query "which public contracts depend on property P of entity E" returns,
for each affected entry, the edge chain with each edge's mode, provenance
and coverage status. This is the explanation the report renders (for
example: public function → by value → nested field → changed layout).
ADR-052's impact model owns rendering. This ADR owns only the query's
content.

### D4. Role- and mode-aware structural diff

`diff_source_graph` gains a *changed-relation* result (same
`(src, dst, kind)`, different role or mode). It neither turns that into a
removed+added pair nor drops it. Existing consumers of the coarse
`added`/`removed` sets are unaffected (ADR-046 D1's promise is kept).

### D5. Body coverage per declaration

A header pass records, for each inline or template body, an ADR-063
`Fact[T]`: `PRESENT` with the body digest, or a reason it was not seen.
A body-dependent edge may be `proven_absent` only when that declaration's
body is `PRESENT` on both sides. Otherwise it stays `unknown`.

### D6. Facts from the same parse

New modes and families come from compact projections of the existing L2
AST parse ([`l4-l2-extraction-convergence.md`](../plans/l4-l2-extraction-convergence.md)).
That includes the template/override/callback facts the projection drops
today ([known gap](../known-gaps/l2-header-projection-drops-template-override-callback-facts.md)).
No rule may add its own clang invocation.

## Prerequisites (tracked elsewhere, not reopened here)

- `requires`-constrained overload `EntityId` collisions and the
  out-of-line member-template scope split (known-gaps, "EntityId scope
  collisions in template declarations"). Propagating along ambiguous
  identities would make both false positives and false suppressions worse.
- Per-member relevance: keep `proven_unreachable` findings out of member
  verdicts (known-gaps, "A directory `compare` still feeds the union `-H`
  set…").
- Enum/typedef paths moved onto `type_reachability` (known-gaps, "Type
  reachability (direct vs. transitive stdlib references)").

## Out of scope

- **Compiler witnesses** for overload resolution, ADL, concepts and implicit
  conversions stay governed by
  [ADR-060](060-synthetic-consumer-compile-probe-deferral.md). This ADR
  supplies the `CONSTRAINT` mode and a proof path a future witness can
  attach to. It does not reopen the deferral. Note on verdict mapping for
  ADR-060: a witness proves that one break exists, and the absence of a
  witness proves nothing about all consumers.
- **Macro-configuration matrices** (comparing the contract under N declared
  `-D` sets) extend [ADR-074](074-logical-macro-definitions-on-the-cli.md)
  and [ADR-065](065-comparison-scope-selection-and-completeness.md)
  variants. They need their own plan once D1–D3 give a per-configuration
  contract.

## Validation

Each mode/contract row ships the full workflow: an independent example
(`examples/` plus `catalog/ground_truth.json`), extraction, a storage round
trip, detector, policy, then the rendered report. It must also include a
near-miss *safe* case (for example, the same layout change behind
`OPAQUE_HANDLE` that must not gate) and incomplete-input variants that must
come out `UNKNOWN`, not `NONE`. Both GCC- and Clang-built fixtures are
required where layout is involved.

## Consequences

- Fewer false positives come from scoping propagation by sensitivity rather
  than by reachability. False-cleans close through per-body coverage and
  role/mode diffs.
- The schema changes: `ExposureMode` on persisted dependency facts and
  body-coverage facts mean a snapshot schema bump and a migration (ADR-062).
- The sensitivity table becomes a reviewed, test-enforced artifact. A new
  `ChangeKind` family must declare its row, the same way a new kind
  declares its `default_verdict`.

## Cross-references

[ADR-044](044-reachability-aware-suppression.md),
[ADR-046](046-source-graph-identity-v2-and-evidence-merge.md) D1/D3/D5,
[ADR-052](052-unified-impact-assessment-model.md),
[ADR-060](060-synthetic-consumer-compile-probe-deferral.md),
[ADR-063](063-one-semantic-pipeline.md),
[ADR-067](067-change-intent-acknowledgment-and-disposition-audit.md),
[G29](../plans/g29-impact-analysis-layer.md) Phase 5,
[`l4-l2-extraction-convergence.md`](../plans/l4-l2-extraction-convergence.md).
