---
doc_type: explanation
audience:
  - contributor
  - library-maintainer
level: advanced
canonical_for:
  - impact-analysis
depends_on:
  - abicheck/impact/consumer_graph.py
  - abicheck/impact/model.py
  - abicheck/impact/engine.py
  - abicheck/buildsource/graph_impact.py
  - abicheck/junit_report.py
lifecycle: active
generated: false
---

# Unified Impact Assessment

abicheck's reachability, graph-proof-path, and suppression-decision signals
for a single finding used to live in several independently-set fields on
that finding, with no shared object to query them through. This page
documents `impact_assessment` — the unified, queryable view over those
fields — and `reachability_state`, the tri-state reachability signal it made
visible in JSON/SARIF output for the first time.

## What reachability means

A finding's subject is **reachable** when a walk from the public surface —
public headers, exported symbols, and the bodies of public inline functions
— arrives at it: changing it is exactly as breaking as changing a public
declaration, whatever namespace it lives in. It is **unreachable** when the
walk positively established that nothing public depends on it, which is the
only state in which suppressing a break there is safe. And it is
**unknown** when no walk reached a verdict, or the only evidence available
is itself flagged narrowed or degraded for the relevant edge family — an
honest "we did not check", never a synonym for safe
([Graph Coverage & Negative Evidence](graph-coverage.md) is why the two must
not be conflated). Before this tri-state existed a consumer saw only a
boolean that was `false` for both of the last two.

The report fields that carry this — `reachability_state`, the
`impact_assessment` object that bundles the reachability, proof-path,
root-cause and suppression-decision fields, and their SARIF spellings — are
documented field by field in
[Source Graph Schema § `reachability_state`](../reference/source-graph-schema.md#reachability_state)
and [§ `impact_assessment`](../reference/source-graph-schema.md#impact_assessment).

## Root-cause grouping

`--report-mode root-cause` groups findings that share a root cause —
today, findings whose `caused_by_type` names the same internal entity, or
that share a `symbol` another finding's `caused_by_type` actually
references (see `reporter_markdown._root_cause_key_and_display` for the
exact grouping rule). JSON nests grouped findings under a `root_causes[]`
array; markdown renders one section per root cause; SARIF and JUnit keep
their existing flat shapes and instead add `rootCauseId`/`rootCause` to
each result/failure (SARIF: `properties.rootCauseId`/`properties.rootCause`;
JUnit: attributes directly on `<failure>`) — restructuring either format's
established shape would break every existing consumer's parsing
assumptions. `root_cause_id` is a stable hash of the grouping key, so it is
consistent across JSON/markdown/SARIF/JUnit for the same underlying report —
none of the four formats can disagree about which findings share a root
cause.

When a `root_causes[]` group is also one of the `RootCauseCorrelator`'s own
multi-piece groups (see `root_cause_evidence` above), the group entry gains
`strongest_evidence_level`/`evidence_levels` — the group-level counterpart
of each member finding's own `impact_assessment.root_cause_evidence`.
Absent for a group `--report-mode root-cause` groups by the broader
`caused_by_type`/`symbol` rule but that the narrower, four-kind correlator
doesn't cover.

JUnit's own `<testcase>` groups by *symbol*, not by finding
(`_partition_changes`), so a symbol with more than one change gets multiple
`<failure>` children under one `<testcase>` — each carries its *own*
change's `rootCauseId`/`rootCause` independently. There is no merging and so
no "what if this testcase's findings disagree on root cause" question: two
sibling `<failure>` elements can legitimately show two different root
causes.

## Why a consumer required a symbol

`compare --used-by <app>` reports the symbols an application binary needs
that the new library no longer exports, as
`consumer_required_symbol_removed` findings. On its own that answers *what*
broke — `training-service` requires `_ZN6detail21train_ops_dispatcherEv` —
but not why the application ever depended on an internal dispatcher it never
called.

When the **old** library's snapshot carries an L5 source graph (`dump
--sources`/`--build-info`, or the always-on header-only graph), abicheck
folds the consumer's own requirements into a copy of that graph and walks
back through it: symbol → declaration (`source_decl_maps_to_symbol`) →
whichever public entry point reaches that declaration in the call graph. The
finding then carries the answer in the fields this page already documents —
`impact_assessment.proof_path.root` names the public entry, `steps` is the
chain, and the prose reads

> `training-service` requires `_ZN6detail21train_ops_dispatcherEv` via public
> entry `train`: train → detail::train_ops_dispatcher → …

The walk uses the same restricted traversal as internal-leak findings, so it
stops at any declaration whose body a consumer does not compile — an
ordinary out-of-line exported function's internal calls are never attributed
to code that cannot see them. That also means a public entry whose body is
*not* consumer-compiled yields no answer rather than a speculative one, as
does a missing graph, a symbol with no captured declaration, or no path from
any entry. In every one of those cases the finding is exactly what it was
before: absence of an explanation is never evidence that a dependency is
absent.

This is [ADR-057](../contribute/adr/057-consumer-graph-and-impact-join.md)
(G29 Phase 4, slice 1). It adds no `ChangeKind`, changes no verdict, and adds
no report field — it fills in fields the schema already had.

## What this does not cover yet

`impact_assessment` does not (yet) include a list of affected consumers or
use cases, or a coverage summary. The consumer *graph* exists (above), but as
evidence a finding is enriched from — not as its own
`affected_consumers`/`affected_use_cases` fields. Declared use cases
(an optional `impact-use-cases.yaml` manifest, `abicheck.impact.use_cases`)
are now also graph-buildable and joinable the same way — see
[Use-Case Impact](../contribute/use-case-impact.md) — but, same as the consumer
graph, only as evidence, with no report field or finding reading it yet.
Runtime-trace ingestion (for either graph) and the per-role coverage matrix
being wired through the impact layer are the remainder of G29 Phase 4. `root_cause_id`/`impact_group_id` (documented above) are implemented,
but `impact_group_id` is currently only ever an alias of `root_cause_id` —
distinguishing them (e.g. bucketing several distinct root causes that share
one broader consumer-visible event under one group while keeping their own
individual root-cause identities) is still open; the `RootCauseCorrelator`
(G29 Phase 6) that would drive that distinction is implemented and now wired
into the report surface as `root_cause_evidence` (documented above), but it
doesn't yet feed `impact_group_id` itself — that would need the correlator's
groups to actually re-bucket findings rather than only annotate them, a
distinct follow-up. Computing `root_cause_id` needs whole-`DiffResult` context (which
findings elsewhere reference this one) that a single finding's read view
can't see on its own — the caller resolves it per report/scope
(`reporter_markdown.root_cause_lookup_for_changes`) and passes it in; see
the [Detector Impact Contract](../contribute/detector-impact-contract.md)
for why the underlying grouping stays a report-level decision
(`--report-mode root-cause` above) rather than something a detector sets
directly. Adding empty placeholder fields for data no producer can populate
would misrepresent what abicheck actually knows, so unimplemented fields are
left out of the schema entirely rather than always-`null`. See
[ADR-052](../contribute/adr/052-unified-impact-assessment-model.md) for the
full list of what this slice deliberately does not implement.

---

**Ladder:** ← [Graph Coverage & Negative Evidence](graph-coverage.md) · Concepts c3 · Internals · [Series overview](abi-api-handling.md) →
