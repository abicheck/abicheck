---
doc_type: how-to
audience:
  - library-maintainer
level: intermediate
summarizes:
  - public-surface
lifecycle: active
generated: false
---

# API Surface Intelligence

abicheck does not only diff symbols one at a time — it also reasons about the
*shape* of your public API as a typed declaration graph. This page describes the
**idiom-aware** features introduced in [ADR-027](../development/adr/027-api-surface-intelligence.md):
single-snapshot surface metrics, idiom & anti-pattern recognition, and
**pattern-aware verdicts** that modulate a diff using that knowledge.

These metrics and idioms run over your **public surface** — see
[What Is Part of Your ABI Surface?](../concepts/abi-surface.md) for what
counts as public vs. internal and how scoping decides it.

> **History note:** this used to be a standalone `abicheck surface-report`
> command. The ADR-043 CLI reset removed it with no direct replacement command
> (it was judged below the five-command bar) — the underlying functions below
> remain directly callable from Python for anyone who needs a single-snapshot
> report outside of a `compare`.

These features are **opt-in** and **auditable**. The governing rule, inherited
from the public-surface work in ADR-024, is:

> Pattern inference may **demote with a disclosed reason** or **raise** a
> finding; it may **never silently delete** one.

Every modulation is recorded, attributed to the rule that made it, and
reversible with a flag.

## Surface metrics — describe one library's surface

```python
from abicheck.serialization import load_snapshot
from abicheck.surface_graph import build_surface_graph, compute_surface_metrics
from abicheck.idioms import recognise_idioms, detect_antipatterns

snap = load_snapshot("libfoo.so.abi.json")  # from `abicheck dump`
graph = build_surface_graph(snap)
metrics = compute_surface_metrics(snap)     # header coverage, fan-in, undocumented-export ratio
idioms = recognise_idioms(graph)            # opaque pointer / PIMPL / handle / factory / callback ABI
antipatterns = detect_antipatterns(graph)   # std:: by-value crossings, missing virtual dtor
```

Dump the library first (`abicheck dump libfoo.so -H include/ -o libfoo.so.abi.json`),
then run the snippet above. It reports, for a single library (no diff):

- header→symbol coverage and the undocumented-export ratio,
- per-type fan-in (the "blast radius" if a type changes),
- recognised **idioms** (`--idioms`): opaque pointer, PIMPL, handle, factory,
  create/destroy pairs, callback ABI, and
- **anti-patterns** (`--anti-patterns`): `std::` types crossed by value, and
  polymorphic types with no virtual destructor.

## Idioms

An *idiom* is a graph pattern recognised conservatively from declaration facts
(pointer depth, fields, bases, vtables, typedef targets). The two that drive
verdict modulation are:

- **Opaque pointer** — a type whose complete definition is **not** visible in
  the public include closure (it is only ever forward-declared) and that public
  functions cross only by pointer. Callers provably cannot `sizeof` or embed it,
  so a change to its size or fields is not an ABI break *for them*.
- **PIMPL** — a *complete* public wrapper whose only data member is a pointer to
  a hidden implementation type. The wrapper's own layout is part of the ABI; only
  the hidden pointee is invisible to callers.

## Pattern-aware verdicts (`--pattern-verdicts`)

When enabled, a post-processing pass modulates findings using the idiom
evidence from **both** snapshots:

| Rule | Effect | Guard |
|------|--------|-------|
| Opaque-pointer layout | A layout change on a provably-opaque type is demoted to compatible (`opaque-by-construction`). | Only when the definition is hidden on **both** snapshots, and only at the `header_aware` evidence tier. |
| PIMPL pointee-only | A change to the hidden impl pointee is demoted (`pimpl-impl-hidden`). | The wrapper's own layout must be byte-identical across both snapshots; a change to the wrapper stays breaking. |
| Anti-pattern raise | A finding on an STL-by-value / non-virtual-dtor surface is annotated with elevated risk. | Pure annotation — it can never hide a finding. |

And it **raises** new breaks when a guarantee callers relied on is lost:

- `opaque_invariant_broken` — a type that was opaque/PIMPL now exposes its
  layout (its definition became visible, or it is now passed by value). Emitted
  **instead** of any silent demotion.
- `handle_type_changed` — an opaque handle typedef's underlying token type
  changed observably.

### Auditability

Every modulation is disclosed:

- a `pattern_modulations` array in the JSON report
  (`{symbol, original_category, new_category, rule_id, reason, evidence_tier,
  edges_matched}`), and the demoted finding stays in `changes` with its
  `effective_verdict` / `modulation_reason` recorded — re-categorised in place,
  never dropped;
- `--explain-patterns` prints the idiom evidence behind each modulation;
- `--no-pattern-verdicts` (the default) disables all modulation, restoring pure
  kind-based classification.

Demotion is gated to the `header_aware` evidence tier (idioms need the AST), a
demotion never overrides a frozen-namespace break, and a break-demotion is
logged at `WARN`. The anti-hiding contract is enforced by the test suite:
a real layout break on a **non**-opaque type still fires at full severity, and a
type that *loses* opaqueness emits `opaque_invariant_broken` rather than being
quietly demoted.

## Cross-library reachability (A3, multi-binary releases)

In a multi-library / bundle `compare` run (directory or package inputs), a type
changed in one library that is also
referenced by a sibling is reported as `bundle_intra_type_changed`. A3 adds a
**reachability filter**: if the consumer library references the changed type
only through its *internal* (non-exported) symbols — so the change cannot reach
the consumer's own public ABI surface — the finding is **demoted to risk**
(reason `consumer-internal-use`), never dropped. When the type leaks into a
symbol the consumer itself exports, the finding stays a full-confidence
cross-DSO break. The demotion is carried on the `BundleFinding` and propagated
onto the lowered `Change`, so the **bundle verdict** and the `compare` exit
code honour it — the same demote-don't-delete contract as A4.

## Surface-metric drift (A1, `--surface-metrics`)

`compare --surface-metrics` emits aggregate, informational `COMPATIBLE`
roll-ups — `public_surface_grew` / `public_surface_shrank` and
`undocumented_export_ratio_increased` — computed from the same
`compute_surface_metrics()` used above. They never drive a verdict on their own (the individual
additions/removals are reported per-symbol); they are a trendable signal for CI
dashboards and release notes.
