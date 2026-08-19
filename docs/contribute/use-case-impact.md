---
doc_type: contributor
audience:
  - contributor
level: intermediate
summarizes:
  - impact-analysis
lifecycle: active
generated: false
---

# Use-Case Impact

> **Preview: a `compare`-native report block, but no finding kind yet.**
> `abicheck compare --use-cases MANIFEST` (below) folds a manifest into a
> real comparison and reports, per declared use case, which of that
> comparison's findings its own resolved entrypoints reach — emitted as the
> report's `use_case_impact` block (schema 2.39) and as a section of the
> text/markdown report. It genuinely answers "was this use case affected",
> but it is read-only: no `Change` field, no exit-code contribution, and
> still no `USE_CASE_IMPACT_CONFIRMED` finding kind (see "What this does
> not cover yet" below). `abicheck project validate-use-cases` checks a
> manifest's structure on its own.

An optional `impact-use-cases.yaml` manifest lets you declare a project's own
business/runtime use cases — "the training workflow", "the batch export
job" — and which public entry points and tests exercise each one. abicheck
promotes the manifest to graph facts and joins them onto the library's own
[unified impact-assessment graph](../learn/impact-analysis.md), the same way
[`--used-by` promotes a real consumer binary's requirements](../use/appcompat.md).

This is [G29 Phase 4 slice 2](../contribute/plans/g29-impact-analysis-layer.md),
amending [ADR-057](../contribute/adr/057-consumer-graph-and-impact-join.md).
`abicheck.impact.use_cases` parses the manifest, builds/joins the graph
facts, and (`explain_use_case_impact`) answers which declared use case(s)
reach a given changed symbol. Two CLI surfaces use it:
`abicheck project validate-use-cases <manifest>` checks the manifest's own
structure, and `abicheck compare --use-cases <manifest> OLD NEW` resolves
each use case's entrypoints against the comparison's own snapshots and
reports which of its findings each use case reaches
(`impact/use_case_impact.py`). There is still no per-`Change`
`affected_use_cases` field and no `USE_CASE_IMPACT_CONFIRMED` finding kind
— see "What this does not cover yet" below.

## Checking a manifest with the CLI

```console
$ abicheck project validate-use-cases impact-use-cases.yaml
use-case manifest validation: impact-use-cases.yaml
OK — 2 use case(s), structurally well-formed.

$ abicheck compare libtraining-1.0.abi.json libtraining-1.1.abi.json \
    --use-cases impact-use-cases.yaml
...
Use-case impact (impact-use-cases.yaml):
  training workflow: 1 change(s)
    - legacy_helper (func_removed)
    unresolved entrypoints: legacy_train_v1
  2 of 3 change(s) reached by no declared entrypoint (absence of proof, not
  proof of absence).
```

`project validate-use-cases` checks only the manifest's own structure (a
non-mapping entry, an unrecognized field, or a missing `use_case` name is a
usage error, exit 64).

`compare --use-cases <manifest>` does the resolution and the attribution
together, against the comparison it is already running. At least one side
must carry a source graph — a `dump --sources`/`--build-info` snapshot, or
any snapshot carrying the always-on header-only graph — and a pair with
none on either side is a usage error rather than a silently missing
section. Entrypoints are resolved reusing the exact same join
`build_use_case_graph` performs internally, so the report can never
disagree with what the comparison itself sees; an unresolved entrypoint is
reported, never treated as a failure — the same "absence is not evidence of
a wrong answer" discipline the rest of this page documents (see "Declared
vs. observed use" below).

Each side is explained against *its own* graph and the two are unioned per
symbol: a symbol added on the NEW side never existed in OLD's graph at all,
so attributing only against OLD would read every addition as unattributed
regardless of whether the NEW side's graph proves it reachable. A change
whose symbol none of a use case's entrypoints can be shown to reach
(directly, or transitively through a consumer-compiled inline/template
body) is simply absent from that use case's list, and counted in
`unattributed_changes`. None of this ever moves a verdict or an exit code:
zero attributed changes just means none of the changes touch a declared use
case's own surface, reported as such rather than silently omitted.
Two structural limitations carry over from
[`--used-by`'s own consumer-impact walk](../use/appcompat.md), since both
reuse the identical graph primitives: only a function/variable-shaped
change (one a `SOURCE_DECL_MAPS_TO_SYMBOL` edge backs) can ever be named —
a type layout change has no such edge to resolve through — and only a
*consumer-compiled* entrypoint (an inline/template body, not an ordinary
out-of-line exported function's own internal calls) can walk transitively
past its own declaration.

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
  `source_decl` node whose own declared visibility is public. Deliberately
  restricted to these two kinds — a public type (`record_type`/`enum_type`/
  `typedef`) is never a valid entrypoint target, since it has no outgoing
  call-graph edge for a consumer-impact walk to follow. A
  name can be spelled either as the graph's internal node id
  (`binary_symbol://_ZN6detail4evalEv`) or as the node's plain label
  (`_ZN6detail4evalEv`, `train`). An exact node id always resolves and
  always takes precedence over a label. A plain label resolves **only**
  when exactly one public node in the graph carries that label — a label
  two or more public nodes share (a common shape for overloaded C++
  entries) is genuinely ambiguous and is treated the same as an
  unresolvable name (below), never guessed at.
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

The document's own shape is validated as a hard error, raising
`abicheck.errors.UseCaseManifestError` rather than silently dropping or
misreading the bad entry — silently accepting a malformed manifest could
make a use case's declared coverage quietly disappear or misresolve from
every future run with no indication why. Rejected:

- invalid YAML syntax, or a mapping that repeats a key (e.g. two
  `entrypoints:` lines pasted into one entry — YAML's own default keeps
  only the last value) or uses an unhashable key (a YAML sequence used as a
  mapping key);
- a top-level document that isn't a YAML list, or a list entry that isn't a
  mapping;
- an entry with an unrecognized field — only `use_case`/`entrypoints`/
  `tests` are accepted, so a misspelling (`entrypoint` instead of
  `entrypoints`) fails loudly instead of silently contributing nothing;
- a missing or blank `use_case` name;
- an `entrypoints`/`tests` value that isn't a YAML list of strings.

An empty document (no file content at all) is a valid, empty manifest —
no use cases declared, not an error.

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

- **No per-finding field.** `impact_assessment` (see
  [Unified Impact Assessment](../learn/impact-analysis.md)) has no
  `affected_use_cases` field yet. `compare --use-cases` emits the
  attribution as one report-level `use_case_impact` block (schema 2.39) and
  a text/markdown section, never attached to an individual `Change` object,
  and SARIF/JUnit carry nothing. The use-case graph (and now
  this read-only explain step) exist as evidence a future `Change`-level
  field could be built from, the same position the consumer graph was in
  before ADR-057's D5/D8 wiring enriched `CONSUMER_REQUIRED_SYMBOL_REMOVED`
  and shared `FUNC_REMOVED`/`SYMBOL_REMOVED` findings with it.
- **No new `ChangeKind` or verdict.** `USE_CASE_IMPACT_CONFIRMED` (G29 Phase
  6) is a planned report-level overlay, not a raw break — matching how
  `CONSUMER_IMPACT_PATH_CONFIRMED` is scoped for the consumer side.
- **Runtime-trace ingestion.** As above — `TRACE_OBSERVED_ENTRY`/
  `TRACE_OBSERVED_EDGE` are reserved vocabulary with no producer.
- **Multiple use-case manifests, or a manifest embedded in `.abicheck.yml`.**
  Only a single, standalone YAML file loaded explicitly via
  `load_use_case_manifest` is supported.
