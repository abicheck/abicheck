# ADR-057: Consumer Graph and the Consumer/Source Impact Join (G29 Phase 4, slice 1)

**Date:** 2026-08-04
**Status:** Accepted — slice 1 implemented (`abicheck/impact/consumer_graph.py`,
the `CONSUMER_*` graph vocabulary in
`abicheck/buildsource/graph_facts.py`, ADR-046 D6's tier-1 "consumer-proven"
selector in `abicheck/buildsource/graph_impact.py`, and the
`abicheck/appcompat.py` wiring that puts the answer on the
`CONSUMER_REQUIRED_SYMBOL_REMOVED` overlay). **Slice 2 (2026-08-09) also
implemented**: the declared-use-case half of Phase 4 —
`abicheck/impact/use_cases.py`, the `USE_CASE_*`/`test_case` graph
vocabulary (`USE_CASE_NODE_KINDS`/`USE_CASE_EDGE_KINDS` in
`abicheck/buildsource/graph_facts.py`), and an optional
`impact-use-cases.yaml` manifest (`use_case`/`entrypoints`/`tests`) that
`build_use_case_graph`/`join_use_case_graph` promote to graph facts and join
onto the library graph, mirroring slice 1's build/join API and mutation-safety
discipline exactly. See "Slice 2: declared use cases" below.
**Update (2026-09-02):** `abicheck/impact/use_case_impact.py`'s
`build_use_case_impact` (engine for `compare --use-cases MANIFEST`) is a
further, real step past graph-building alone — a genuine report-level
surface (`DiffResult.use_case_impact`, emitted by the full JSON path and
folded into text/markdown/review output — `cli_compare_fold.
_USE_CASE_IMPACT_BEARING_FORMATS`; the summary/`stat` JSON path and
sarif/junit/html deliberately carry none of it, and `compare` rejects
`--use-cases` outright when no selected output format is in that set)
attributing a real comparison's changed symbols to the declared use cases
whose entrypoints reach them. This is
**not** the same thing as the two items still **not implemented**: runtime-
trace ingestion (the `TRACE_OBSERVED_ENTRY`/`TRACE_OBSERVED_EDGE` reserved
edge kinds stay unimplemented), and a per-finding, report-*schema*
`Change.affected_use_cases` field or `USE_CASE_IMPACT_CONFIRMED` finding
kind wired into `compare`'s own change objects/exit code (that needs its
own schema bump and FP-gate examples — see
`abicheck/impact/use_cases.py`'s own "Not implemented in this slice" note).
The three `CONSUMER_*` reserved edge kinds from slice 1
(`CONSUMER_INSTANTIATES_DECL`, `CONSUMER_COMPILED_FROM_HEADER`,
`RUNTIME_FAILED_TO_RESOLVE_SYMBOL`) also remain unimplemented. See
"Deliberately not implemented this slice".
**Decision maker:** (pending — recorded per repository convention;
implemented under [G29](../plans/g29-impact-analysis-layer.md) Phase 2's own
"needs its own ADR before implementation starts" gate, which
[ADR-044](044-reachability-aware-suppression.md)'s "Post-merge review rounds"
note sets for any change to graph identity or suppression-adjacent semantics)

---

## Context

`compare --used-by <app>` has scoped a library diff to one real consumer
binary since [ADR-005](005-application-compat-check.md), and
[ADR-044](044-reachability-aware-suppression.md) P2 promoted an uncovered
missing symbol from a bespoke report string to a first-class, suppressible
`CONSUMER_REQUIRED_SYMBOL_REMOVED` finding. But the mechanism underneath it
is a **set intersection**: `appcompat.parse_app_requirements` reads the
consumer's undefined-symbol table, subtracts the new library's exports, and
reports the difference. So the strongest thing the finding can say is

> `training-service` requires missing symbol
> `_ZN6detail21train_ops_dispatcherEv`

which tells a maintainer *that* they broke a consumer and nothing about
*why the consumer ever depended on that symbol*. For an internal, exported
dispatcher — the exact oneDAL-shaped case
[ADR-041](041-compiler-facts-semantic-impact-graph.md) opens with — the answer
is neither obvious nor guessable from the name: the consumer never called it,
it called public inline `train()`, whose body it compiled, and *that* body
called the dispatcher.

The library side already has all the evidence needed to say so. The L5 source
graph carries `SOURCE_DECL_MAPS_TO_SYMBOL` (symbol → declaration) and
`DECL_CALLS_DECL` (the call graph), and `internal_leak.py` already walks
exactly that chain — restricted by ADR-046 D5's `CALL_GRAPH_TRAVERSAL_POLICY`
— to produce internal-leak proof paths. What was missing is the consumer end:
nothing in the graph knew a consumer existed, so the two bodies of evidence
sat side by side and never met.

That absence also left one concrete hole elsewhere. ADR-046 D6 defines a
six-tier proof-path preference order whose **strongest** tier is
"consumer-proven", and `graph_impact.select_preferred_graph_path` implements
tiers 2–6 with a comment saying tier 1 "needs a consumer graph that doesn't
exist yet (Phase 4)". The top of the preference order has been
unreachable since it was written.

## Decision

### D1 — A required symbol is an *edge*, not a node kind

G29's plan sketch names a `consumer_required_symbol` node kind. **Rejected.**
A required symbol given its own node kind produces a consumer graph and a
library graph that are structurally similar and completely disjoint — they
would have to be re-joined by some later name-matching pass, which is exactly
the fragile string-matching the graph exists to replace.

Instead `build_consumer_graph` emits a `CONSUMER_REQUIRES_SYMBOL` edge from
the `consumer_binary` node **onto the ordinary
`binary_symbol://<symbol>` node id the library's own graph already uses**.
The shared node id *is* the join, and ADR-046 D2's evidence-preserving merge
does the rest: the node ends up carrying both producers' facts, so
`{f.producer for f in node.facts}` answers "who says this symbol matters" with
no extra machinery.

Node kinds added: `consumer_binary` (populated), `consumer_object` and
`runtime_probe` (reserved). Edge kinds added: `CONSUMER_REQUIRES_SYMBOL`,
`CONSUMER_REQUIRES_VERSION` (populated), `CONSUMER_INSTANTIATES_DECL`,
`CONSUMER_COMPILED_FROM_HEADER`, `RUNTIME_FAILED_TO_RESOLVE_SYMBOL`
(reserved — same "registered so a hand-built or newer graph naming one is
never rejected, but no normalized data source yet" pattern ADR-041 P1 #2 used
for the archive/linker kinds).

A `CONSUMER_REQUIRES_VERSION` edge targets an `external_dependency` node keyed
on the bare `DT_NEEDED` soname, matching how `build_source_graph` already keys
a target's non-project dependencies — so a consumer's `DT_NEEDED` and a build
target's dependency on the same library fold onto one node rather than two
spellings of it.

**Where the vocabulary lives.** In `buildsource/graph_facts.py` (the leaf that
already owns `GraphNode`/`GraphEdge`), unioned into
`source_graph.NODE_KINDS`/`EDGE_KINDS`. Two reasons, both structural rather
than aesthetic: `source_graph.py` sits at its 2000-line AI-readiness hard cap
(1999 after this change), and the producer imports `source_graph`, so
`source_graph` cannot import the producer back without a cycle.

### D2 — The join answers with the library's own restricted walk, not a new one

`explain_required_symbols` reuses `internal_leak._consumer_compiled_reachability`
under `CALL_GRAPH_TRAVERSAL_POLICY` rather than running a fresh BFS. This is
not code-reuse convenience — it is the correctness requirement. An ordinary
out-of-line exported function's body is compiled into the *library*, never
into a consumer, so an unrestricted walk would attribute a purely internal
implementation-detail call to code that cannot see it. Reusing the one policy
also guarantees that a proof path shown on a consumer finding can never
contradict one shown by an internal-leak finding over the same graph.

Two consequences fall out of the shared predicate and are handled explicitly:

- **"Direct requirement" is keyed on declared visibility, not on
  `is_consumer_compiled_public_entry`.** That predicate treats *any* exported
  declaration as public — true of every symbol reaching this code by
  construction, since a consumer can only require something the library
  exported. Using it to detect "the consumer names this symbol directly" would
  classify every requirement as direct and the walk would never run, which is
  precisely the internal-exported-dispatcher case this join exists for. A
  requirement is direct when the declaration's own node visibility is in
  `PUBLIC_VISIBILITIES` (a public header declares it).
- **The target's own declaration is excluded from the walk's entries** for the
  same reason: it qualifies as an "entry" by the exported-decl rule, and a
  zero-hop self-path explains nothing.
- **A direct requirement is answered even when there is nothing to walk from**
  (Codex review). A library whose every exported declaration is an ordinary
  out-of-line function has no consumer-compiled public entry at all, so the
  entry set is empty — but that only blocks the *walk*. Classifying a direct
  requirement needs nothing but the declaration's own visibility, so bailing
  out on an empty entry set lost the proof path for the most ordinary case
  there is: a removed public function the consumer named directly. The empty
  check now guards the walk, not the whole answer.

The API is **batch-first** (`explain_required_symbols`), with the
single-symbol form a thin convenience over it. The expensive half — the BFS
from every public entry — does not depend on which symbol is being explained,
and a library shedding many symbols at once is exactly when re-walking per
symbol is least affordable. The walk is also computed lazily, so a run whose
every missing symbol turns out to be a direct public entry never pays for it.

### D3 — The join is a deep copy; the library graph is never mutated

`join_consumer_graph` deep-copies the library graph before folding the
consumer in. Shallow re-registration would have been the obvious
implementation and is wrong: `SourceGraphSummary.add_node` merges into the
**stored** object in place (ADR-046 D2), so registering the library's own
`GraphNode` objects into a fresh summary leaves them shared, and the
consumer's facts land on the library graph's own nodes — leaking one
`--used-by` application's requirements into every unrelated analysis of the
same snapshot (`internal_leak`'s walks, `source_graph_findings`' diff). The
regression test asserts object non-identity and fact absence, not just node
counts, because every count is identical under the buggy version.

The joined graph is transient (walked and rendered, never serialized), so it
is deliberately not `finalize()`d: recomputing a `graph_id`/`coverage` block
for it would only produce a second, slightly different description of the same
extraction with nothing to read it. The library's `coverage`/
`extractor_passes`/`narrowed_passes`/`degraded_passes` carry over unchanged —
the consumer side adds no source-extraction coverage, and rewriting those
would make the library's own honesty flags describe a pass that never ran.

### D4 — ADR-046 D6 tier 1 becomes computable, conservatively

`select_preferred_graph_path` now reads the consumer-required node set off the
graph it was already given, so tier 1 needs no new parameter and is **inert**
— an empty set, behaving exactly as before — for every graph without consumer
facts folded in, which is every run without `--used-by`.

Tier 1 matches on the path's **endpoint**, not on any node along it
(CodeRabbit review): the tier answers "is this path's *subject* something a
real consumer requires", and a consumer requiring some intermediate hop says
nothing about the entity the path actually points at — matching anywhere would
let a path that merely passes through a required node, then continues on to an
unrelated target, outrank a genuinely exact one.

Tier 1 is also narrower than "the endpoint is consumer-required" alone: the
overapprox check still runs **first** and still wins. A path crossing a
virtual or function-pointer call is an over-approximation of the real dispatch
chain, so the fact that some consumer requires its endpoint says nothing about
whether *that chain* is how it got there. Tier 1 therefore means
"consumer-proven **and** exactly resolved" — the same conservative reading
ADR-046 D5's `effect_transitions` already applies when it refuses to let a
degraded walk present itself as an exact one.

### D5 — Enrichment only, on the existing finding

`appcompat.scope_diff_to_app` attaches the answer to the
`CONSUMER_REQUIRED_SYMBOL_REMOVED` overlay it already builds, via the existing
`graph_impact.attach_impact_metadata` — no new `ChangeKind`, no new finding,
no verdict or severity change, no new report field. The overlay was already
`PROVEN_REACHABLE`/`consumer_proven`; all this adds is *why*, as
`affected_public_roots` + `impact_proof_path` + a prose
`reachability_proof_path`, which `impact.engine.assess_change` already reads
into `ImpactAssessment.proof_path` and `reporter.py`/`sarif.py`/
`junit_report.py` already serialize. **No report-schema bump.**

Ordering matters and is asserted: the enrichment lands *before*
`Change.impact_assessment` is cached (ADR-052 slice 9), since the cache is
built from these very fields — attached afterwards, the proof path would never
reach a report.

Prose formatting reuses `source_graph_findings._format_dependency_path`, so a
consumer proof path reads identically to an internal-leak one for the same
edges.

### D6 — Degrade to no answer, never to a wrong one

Every failure mode returns "nothing to say" and leaves the finding exactly as
it was before this ADR: no L5 graph on the old side, no
`SOURCE_DECL_MAPS_TO_SYMBOL` edge for the symbol, no consumer-compiled public
entry, or no path from one to the other. Absence of a consumer edge is never
evidence of absence of a dependency — the same coverage-honesty rule
[ADR-031](031-source-implementation-graph-augmentation.md) D9 sets for the
rest of the graph.

The **old** library's graph is used, not the new one: the symbol is missing
from the new library by definition, so only the old side still carries the
declaration and call edges that explain the dependency.

### D7 — The old snapshot is threaded separately from the old operand

Post-review addition (Codex on PR #672), and the difference between this
slice working and not working in practice.

`scope_diff_to_app`'s `old_lib` operand is `Path | AbiSnapshot`, and every
caller resolves it the same way — `old_input if
detect_binary_format(old_input) is not None else old_snapshot`
(`cli_compare_helpers._apply_used_by_scoping`, `mcp_server`'s identical line;
`check_appcompat` likewise passes the path it just dumped from). So for a
**real binary OLD** — the primary usage — the operand is a `Path`, and
reading the graph off that operand alone meant the join fired *only* when OLD
happened to be a saved JSON snapshot. That is the inverse of what a user
would expect, and it silently skipped exactly the runs that asked for the
richest evidence: `compare old.so new.so --used-by app --old-sources ...`
produced a full L5 graph and then never consulted it.

The fix is a separate `old_snapshot` keyword, consulted first by
`_library_source_graph` and used for **graph lookup only** — `old_lib` keeps
owning every binary/export/version read, so the two can never disagree about
what the library exports. All three call sites pass the snapshot they already
hold for that same path. `tests/test_consumer_graph.py` pins both halves: the
path-operand-plus-snapshot shape produces the enriched overlay, and the
missing-symbol/verdict/required-symbol results are identical either way.

This is worth stating plainly rather than burying in a changelog line,
because the failure mode was invisible: nothing errored, no test failed, and
the finding simply came out exactly as it did before the feature existed.

### D8 — Enrichment reaches the covered case too, in library-owned wording

Second post-review addition (Codex on PR #672). D5 attached the explanation
to the `CONSUMER_REQUIRED_SYMBOL_REMOVED` overlay — but that overlay only
exists for a symbol `uncovered_missing_symbols()` finds *no* library-diff
`Change` for. An ordinary removed export produces its own `FUNC_REMOVED`,
which covers the symbol, so no overlay is created and nothing was enriched.
The join reached only the case where the diff had already failed to represent
the removal, and missed the ordinary flow — including the
internal-dispatcher case in this ADR's own Context.

So the walk now runs over every missing symbol, and `_enrich_covered_changes`
attaches the explanation to the library-diff finding that already covers it.
Two constraints shape how:

- **The prose is consumer-neutral on a shared finding.** `_partition_app_changes`
  appends the *same* `Change` objects that are in `DiffResult.changes`, which
  the unscoped report also renders. "`training-service` requires X" is a fact
  about one consumer and would be wrong there (and last-writer-wins across
  several `--used-by` apps); "X is reachable from public entry `train`" is a
  fact about the library, true regardless of who consumes it. The overlay —
  which exists per app — keeps the consumer-named wording. Only the sentence
  differs; `affected_public_roots`/`impact_proof_path` are library facts
  either way, and are what `internal_leak` already writes onto shared changes.
- **A finding that already carries evidence is never touched**
  (`_has_impact_evidence`). `attach_impact_metadata` assigns its whole field
  set unconditionally, `None` included, so enriching a finding a producer had
  already populated would erase that producer's work — on an object the
  library-wide report shares. It also makes the multi-`--used-by` case
  first-writer-wins, and therefore deterministic in app order, rather than
  silently last-writer-wins.

The residual asymmetry is accepted and worth naming: a `FUNC_REMOVED` gains a
proof path only on a run that passed `--used-by`, so the same comparison with
and without it renders that finding differently. The alternative — minting
scoped copies so the library-wide report never sees consumer-derived work at
all — collides with `_apply_used_by_scoping`'s `_finding_id`-based
`scoped_only_changes` dedup (an enriched copy shares its original's id and
would be dropped) and needs its own design across all three front ends.

### Slice 2 — declared use cases (2026-08-09 amendment)

Slice 1 joins a real, observed consumer's requirements onto the library
graph. Slice 2 adds the declared, human-authored counterpart the original
plan sketch named but this ADR deferred: an optional
`impact-use-cases.yaml` manifest naming a project's own business/runtime use
cases (`use_case`/`entrypoints`/`tests`), promoted to `use_case`/`test_case`
graph nodes and `USE_CASE_USES_ENTRY`/`TEST_COVERS_USE_CASE` edges.

The shape mirrors slice 1's D1/D3/D6 decisions directly, without needing new
decisions of its own:

- **An entrypoint is an edge onto the library's own existing node**, not a
  parallel node kind — the same D1 reasoning: a `use_case` node names its
  entry points by pointing at the library graph's real `binary_symbol`/
  `source_decl` nodes (matched by id or label), so the join is again one
  shared node id, not a second disconnected graph needing a name-matching
  pass to reunite.
- **The join is a deep copy** (`join_use_case_graph`), for the identical D3
  reason `join_consumer_graph` is: the library graph is shared with every
  other analysis of the same snapshot, and a shallow fold would leak one
  project's declared use cases onto the library graph's own nodes.
- **Degrade to no answer, never a wrong one** (D6): an `entrypoints` name
  the library graph cannot resolve is silently skipped — no node, no edge,
  no error. Only the manifest *document* being structurally malformed (not
  a YAML list, a non-mapping entry, a missing/blank `use_case` name) is a
  hard error (`UseCaseManifestError`), matching `PolicyError`'s "an
  unknown/malformed rule must never be silently skipped" precedent for
  user-supplied YAML, not `consumer_graph`'s degrade-on-absence rule — the
  two are different failure classes (a malformed document vs. one
  unresolvable entry within an otherwise-valid one).

`TRACE_OBSERVED_ENTRY`/`TRACE_OBSERVED_EDGE` remain reserved, unpopulated
vocabulary — runtime-trace ingestion is still out of scope, for the same
reason the original "Deliberately not implemented this slice" section gives:
absence of a trace must never read as "not used", a semantics decision with
no data yet to validate against. This slice adds no CLI flag reading the
manifest, no report field, and no finding enrichment — it is graph-building
only, the same position slice 1 was in before its own D5/D8 wiring. See
[Use-Case Impact](../use-case-impact.md) for the user-facing manifest
format and the declared-vs-observed distinction, and
`tests/test_use_cases.py` for the test coverage.

## Consequences

- The top of ADR-046 D6's preference order is reachable for the first time.
- A `--used-by` finding can name the public API entry behind an internal
  symbol's removal, which is the difference between "your consumer broke" and
  "your consumer broke because `train()` inlines a call to it".
- `impact/consumer_graph.py` declares a `ConsumerRequirements` `Protocol`
  rather than importing `appcompat.AppRequirements`. That is partly a cycle
  constraint (the AI-readiness `import-cycle-growth` gate counts a
  `TYPE_CHECKING`-only import as a real edge, and `appcompat` calls into this
  module) and partly the honest shape: nothing here needs `appcompat`'s
  ELF/PE parsing, only three fields — so a later requirement source (a runtime
  trace, a consumer build's own facts) can supply one without constructing an
  `AppRequirements`.
- `source_graph.py` is now one line under its hard cap. Any further
  vocabulary belongs in `graph_facts.py` and gets unioned in, as this ADR's
  entries are.

## Deliberately not implemented this slice

- **`impact-use-cases.yaml` and the use-case graph** — **implemented in
  slice 2** (2026-08-09 amendment, above): `use_case`/`test_case` nodes and
  `USE_CASE_USES_ENTRY`/`TEST_COVERS_USE_CASE` edges, in
  `abicheck/impact/use_cases.py`; see
  [Use-Case Impact](../use-case-impact.md). `TRACE_OBSERVED_ENTRY`/
  `TRACE_OBSERVED_EDGE` remain reserved, unpopulated — see the next bullet.
- **Runtime-trace ingestion** (`RUNTIME_FAILED_TO_RESOLVE_SYMBOL`,
  `runtime_probe`, and — as of slice 2 — `TRACE_OBSERVED_ENTRY`/
  `TRACE_OBSERVED_EDGE` too). Registered as vocabulary; no producer. The hard
  part is not the edges — it is that absence of a trace must never read as
  "not used", which is a semantics decision this slice has no data to
  validate against.
- **`consumer_object` / `CONSUMER_COMPILED_FROM_HEADER` /
  `CONSUMER_INSTANTIATES_DECL`.** These need *consumer-side* build evidence
  (which TU of the consumer compiled which header, which template it
  instantiated). `AppRequirements` is whole-binary and static; there is no
  normalized data source, and inventing one from symbol names would be exactly
  the guessing this join replaced.
- **Multiple consumers in one graph.** `compare --used-by` takes one
  application; the schema already supports several `consumer_binary` nodes and
  `explain_required_symbols` would need no change, but nothing constructs that
  shape today, so it is untested and unclaimed.
- **An old library with genuinely no snapshot anywhere gets no join.** The
  graph is read off an `AbiSnapshot`'s embedded pack, so a caller that only
  ever had a bare path — and never dumped or loaded a snapshot of it — has
  nothing to offer. `scope_diff_to_app`'s `old_snapshot` parameter (D7 above)
  covers every in-tree caller, all of which *do* hold one; closing the
  residual case would mean `appcompat` dumping a snapshot of its own, a
  different and much more expensive contract than "scope an already-computed
  diff".
- **Scoped copies of enriched library-diff findings.** D8 enriches the shared
  `Change` in place, in library-owned wording. Keeping consumer-derived
  evidence entirely out of the unscoped report instead would need a
  scoped-copy path plus a `_finding_id` dedup rule that lets an enriched copy
  survive alongside its original, in `cli_compare_helpers`, `mcp_server`, and
  the reporters. Its own slice.
- **`case194`** (G29 Phase 6's `consumer → symbol ← public entry` example
  fixture). This slice's end-to-end coverage is at the `scope_diff_to_app`
  level with synthetic snapshots; a real compiled fixture pair belongs with the
  rest of the Phase 6 example catalog, gated by the same
  `ground_truth.json`/FP-corpus checklist.
