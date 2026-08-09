---
doc_type: how-to
audience:
  - library-maintainer
level: intermediate
summarizes:
  - impact-analysis
lifecycle: active
generated: false
---

# Use-Case Impact

An optional `impact-use-cases.yaml` manifest lets you declare a project's own
business/runtime use cases — "the training workflow", "the batch export
job" — and which public entry points and tests exercise each one. abicheck
promotes the manifest to graph facts and joins them onto the library's own
[unified impact-assessment graph](../learn/impact-analysis.md), the same way
[`--used-by` promotes a real consumer binary's requirements](../use/appcompat.md).

This is [G29 Phase 4 slice 2](../contribute/plans/g29-impact-analysis-layer.md),
amending [ADR-057](../contribute/adr/057-consumer-graph-and-impact-join.md).
It is a **graph-building** feature only: `abicheck.impact.use_cases` parses
the manifest and builds/joins the graph facts. There is no CLI flag reading
this manifest yet, no `affected_use_cases` report field, and no
`USE_CASE_IMPACT_CONFIRMED` finding — see "What this does not cover yet"
below.

## Why a separate manifest from `usecase-registry.yaml`

`docs/contribute/usecase-registry.yaml` tracks **abicheck's own** feature
coverage — whether abicheck itself supports header-only analysis, for
example. `impact-use-cases.yaml` tracks **your project's** business/runtime
use cases — whether *your* training workflow calls `train()`. These are
unrelated concepts that happen to share the English phrase "use case";
conflating them into one schema would make "abicheck supports X" and "our
workflow uses Y" read as the same kind of fact. They are deliberately kept
as two separate, unrelated files.

## Manifest format

```yaml
# impact-use-cases.yaml
- use_case: training-workflow
  entrypoints:
    - train
    - _ZN6detail4evalEv
  tests:
    - test_train_end_to_end

- use_case: batch-export
  entrypoints:
    - export_batch
  tests: []
```

Three fields per entry, deliberately minimal — matching exactly what
[the plan](../contribute/plans/g29-impact-analysis-layer.md#phase-4-consumer-use-case-join-slices-1-2-implemented-adr-057)
sketches, nothing more:

- **`use_case`** (required, non-empty string) — the use case's name. Becomes
  the label of a `use_case` graph node.
- **`entrypoints`** (optional list of strings) — public-entry symbol or
  declaration names/labels this use case exercises. Each name is matched
  against the library's own graph: an exported `binary_symbol` node, or a
  `source_decl` (or other) node whose own declared visibility is public. A
  name can be spelled either as the graph's internal node id
  (`binary_symbol://_ZN6detail4evalEv`) or as the node's plain label
  (`_ZN6detail4evalEv`, `train`) — both resolve to the same node.
- **`tests`** (optional list of strings) — free-form test identifiers that
  cover this use case. Recorded as `test_case` nodes with no resolution
  step, since there is no graph node kind an external test identifier could
  fail to resolve against.

An entrypoint name the library graph cannot resolve is **silently skipped**
— no node, no edge, no error, and no signal that the entrypoint is somehow
wrong. This is the same "absence, never a wrong answer" discipline
`abicheck.impact.consumer_graph` (ADR-057 slice 1) already follows for an
unresolvable required symbol: a library graph that's incomplete (header-only,
partial `--sources` coverage, or simply missing that one declaration) is a
far more likely explanation than a genuinely broken manifest entry, and
treating the two the same way would make an ordinary coverage gap look like
a manifest bug.

Only the document shape itself is validated as a hard error: the top-level
document must be a YAML list, each entry a mapping, and each entry's
`use_case` a non-empty string. A malformed manifest raises
`abicheck.errors.UseCaseManifestError` rather than silently dropping the bad
entry — silently skipping a malformed entry could make a use case's declared
coverage quietly disappear from every future run with no indication why.

## Entrypoint mapping and test association

```python
from abicheck.impact.use_cases import (
    build_use_case_graph,
    join_use_case_graph,
    load_use_case_manifest,
)

definitions = load_use_case_manifest("impact-use-cases.yaml")
use_case_graph = build_use_case_graph(definitions, library_graph)
joined = join_use_case_graph(library_graph, use_case_graph)
```

`build_use_case_graph` resolves every `entrypoints` name against
*`library_graph`* — the library's own L5 source graph or header-only graph
(see [Build Info & Sources](../learn/build-source-data.md) for how that
graph gets built in the first place) — and returns a small, standalone
graph of `use_case`/`test_case` nodes and their edges. `join_use_case_graph`
then folds that graph into a **deep copy** of the library graph, mirroring
`consumer_graph.join_consumer_graph`'s identical reasoning: the library
graph is shared with every other analysis of the same snapshot (internal-leak
walks, the source-graph diff, a `--used-by` consumer join), so a shallow
fold would leak one project's declared use cases onto the library's own
public-entry nodes and corrupt every unrelated analysis of the same run.
The join itself is nothing more than registering into the same evidence
store: a node the library graph already has and a use case's edge also
names ends up as one node carrying both producers' facts (ADR-046 D2).

## Declared vs. observed use — and what "no trace" does *not* mean

This slice only ever adds **declared** evidence: a human wrote
`impact-use-cases.yaml` and asserted that a use case exercises certain
entry points. There is no **observed** counterpart yet — no runtime trace
confirms or contradicts a declaration. Two edge kinds are already reserved
in the graph schema for that future work, `TRACE_OBSERVED_ENTRY` and
`TRACE_OBSERVED_EDGE`, but nothing populates them today.

This distinction matters for how you read the graph: the **absence** of a
`use_case` node naming some entry point is not evidence that no use case
depends on it — it only means nobody has written a manifest entry for it
yet (or the manifest doesn't exist at all). Likewise, once trace ingestion
exists, the absence of an observed trace edge will not mean "this use case
doesn't really use this entry" — a trace only ever *positively* confirms
what it happened to observe during one run; it can never prove a codepath
is unused. Runtime-trace ingestion is explicitly deferred (see ADR-057's
"Deliberately not implemented this slice") precisely because getting this
distinction wrong — reading a missing trace as "not used" — is a real
correctness risk with no data yet to validate the right semantics against.

## Full-library vs. consumer/use-case-scoped verdict semantics

Nothing in this slice changes any verdict, finding set, or exit code. The
same rule that governs `--used-by` scoping applies here in advance of any
consumer of this graph existing: a **full-library** `compare` verdict
reflects every change to every declared symbol, regardless of whether any
use case (declared or observed) reaches it. A **use-case-scoped** verdict —
not implemented yet, tracked as G29 Phase 6's `USE_CASE_IMPACT_CONFIRMED`
report-level overlay — would answer a narrower question: does *this*
declared use case's own entry points and their call-graph closure reach the
change at all. The two are not in tension and neither ever silently
replaces the other, the same additive-evidence principle every other
optional L3–L5 layer in abicheck already follows (see
[Build Info & Sources](../learn/build-source-data.md)'s "one rule that
governs everything").

## What this does not cover yet

- **No CLI wiring.** There is no `dump`/`compare` flag that reads
  `impact-use-cases.yaml` today. The Python API above is the only way to
  build and join a use-case graph in this slice.
- **No report field.** `impact_assessment` (see
  [Unified Impact Assessment](../learn/impact-analysis.md)) has no
  `affected_use_cases` field yet — the use-case graph exists as evidence a
  future finding could be enriched from, the same position the consumer
  graph was in before ADR-057's D5/D8 wiring enriched
  `CONSUMER_REQUIRED_SYMBOL_REMOVED` and shared `FUNC_REMOVED`/`SYMBOL_REMOVED`
  findings with it.
- **No new `ChangeKind` or verdict.** `USE_CASE_IMPACT_CONFIRMED` (G29 Phase
  6) is a planned report-level overlay, not a raw break — matching how
  `CONSUMER_IMPACT_PATH_CONFIRMED` is scoped for the consumer side.
- **Runtime-trace ingestion.** As above — `TRACE_OBSERVED_ENTRY`/
  `TRACE_OBSERVED_EDGE` are reserved vocabulary with no producer.
- **Multiple use-case manifests, or a manifest embedded in `.abicheck.yml`.**
  Only a single, standalone YAML file loaded explicitly via
  `load_use_case_manifest` is supported.
