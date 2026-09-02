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

This is slice 1 of [G29 Phase 3](../contribute/plans/g29-impact-analysis-layer.md)
([ADR-052](../contribute/adr/052-unified-impact-assessment-model.md)). It
adds a report-level read view; it does not change which findings are
produced, which are suppressed, or any verdict.

## `reachability_state`

Every finding in a full JSON or SARIF report now carries `reachability_state`
(`sarif`: `reachabilityState`), one of:

- `reachable` — the finding's subject was proven public-reachable (the same
  signal that sets `public_reachable: true`).
- `unreachable` — the reachability walk positively found this finding's
  subject **not** part of the effective public ABI.
- `unknown` — no walk reached a verdict at all, or the only evidence
  available (typically the optional [L5 source graph](build-source-data.md))
  is itself flagged narrowed or degraded for the relevant edge family. See
  [Graph Coverage & Negative Evidence](graph-coverage.md) for why `unknown`
  is not the same claim as `unreachable`.

Before this, a JSON/SARIF consumer could only see the boolean
`public_reachable`, which is `false` for **both** `unreachable` and
`unknown` — there was no way to tell "we checked and it's safe to suppress"
apart from "we never checked, don't assume it's safe." `reachability_state`
closes that gap; it is always present (never an absent key), since
`unknown` is itself a meaningful, honest answer.

## `impact_assessment`

`impact_assessment` bundles the finding's reachability/impact fields into
one object, so a consumer doesn't need to stitch together several
independently-nullable keys:

```json
{
  "reachability_state": "reachable",
  "public_reachable": true,
  "reachability_kind": "value_embedding",
  "confidence": "high",
  "proof_path": {
    "target": "ns::internal::Helper",
    "root": "pub",
    "is_direct": false,
    "prose": "fn:pub → base:detail::Helper"
  },
  "decision": {
    "state": "kept"
  }
}
```

- `reachability_state`/`public_reachable`/`reachability_kind` mirror the
  finding's own top-level fields of the same name.
- `proof_path` mirrors `affected_public_roots`/`impact_proof_path`/
  `impact_is_direct`/`reachability_proof_path`, when the finding has any of
  them — `root` and `steps` come from the structured L5 graph walk
  ([ADR-048](../contribute/adr/048-canonical-entity-identity-and-graph-reconciliation.md)),
  `prose` is the human-readable rendering. `steps` is empty when only the
  prose rendering is available. When a producer had more than one candidate
  path and picked this one via the
  [ADR-046 D6 preference order](../reference/source-graph-schema.md#proof-path-preference-order-adr-046-d6),
  the runner-ups appear as `alternative_paths` (each its own nested
  `proof_path`-shaped object) and `discarded_path_count` counts any further
  candidates beyond the kept cap — both absent for the common single-candidate
  case. `occurrence_id` is a stable, `description`-independent hash over this
  path's underlying graph occurrences
  ([ADR-046 D1](../reference/source-graph-schema.md#relation_key-and-occurrence_id)) —
  absent today for nearly every finding, since no current producer populates
  the per-call-site attrs it's derived from.
- `decision` records whether the finding was kept or suppressed, and (when a
  [pattern-aware modulation](../use/api-surface-intelligence.md) or
  other classification override fired) the reason code and
  `verdict_override` — the overridden verdict, which can be a downgrade
  *or* an escalation (e.g. a `std::`-embedding proof promoting
  `STDLIB_IMPLEMENTATION_CHANGED` to `BREAKING`), not always a demotion.
  `suppression_rule` names the suppression rule that actually suppressed a
  finding (its `label`, falling back to its `reason`) — present only on a
  `suppression.suppressed_changes[]` entry, and only when the matching rule
  set either field.
- `evidence_category`/`correlated_change_kind` mirror the finding's own
  top-level fields when set.
- `root_cause_id`/`root_cause_display`/`impact_group_id` (G29 Phase 3
  follow-up) are this finding's root-cause grouping key/display root — the
  same computation [root-cause grouping](#root-cause-grouping) below uses,
  surfaced per-finding independent of `report_mode`. Present only when the
  finding has a real correlation signal (a `caused_by_type`, or its own
  symbol is referenced by another finding's `caused_by_type`); absent for
  an uncorrelated singleton finding, so a plain finding's
  `impact_assessment` doesn't balloon with a root cause naming nothing but
  itself. `impact_group_id` is currently always identical to
  `root_cause_id` — a placeholder alias until a future revision gives it
  independent meaning.
- `root_cause_evidence` (G29 Phase 6) is this finding's own entry from the
  `RootCauseCorrelator` (`abicheck.impact.correlation.correlate_root_causes`)
  — present only when this finding is a member of one of that composer's
  multi-piece groups: the four load-failure kinds (a symbol vanishing from
  the export table, an internal dependency of a public entry point, a real
  consumer's own unresolved import, and that consumer actually failing to
  load), correlated by shared symbol identity and ranked by evidence
  strength (`artifact_proven` → `call_graph_overapprox` → `call_graph_proven`
  → `consumer_proven` → `runtime_proven`). `evidence_level` is this
  finding's own rank; `strongest_evidence_level`/`evidence_levels` describe
  the whole correlated group, so a consumer can tell "this piece alone is
  only artifact-proven, but the group as a whole also has consumer proof"
  without re-running the correlator itself. Unconditional on `report_mode`,
  same as `root_cause_id`/`impact_group_id`.

`impact_assessment` intentionally duplicates data already published at the
top level — it exists so a consumer can query one object instead of several
separately-named keys, not to replace the existing fields (which stay for
backward compatibility). To keep large reports from filling up with mostly
empty objects, `impact_assessment` is **only emitted when it carries
information beyond the all-defaults case** — a plain finding with no
reachability/impact evidence at all won't have this key, only
`reachability_state: "unknown"`.

Both fields appear everywhere a finding is serialized: the full `changes[]`
list, `--report-mode leaf`'s `leaf_changes[]`/`changes[]` union (root type
changes route through a separate builder that mirrors the same fields), and
each entry in `suppression.suppressed_changes[]` — a suppressed finding's
`decision.state` is always `"suppressed"` there, so its `impact_assessment`
is always present. SARIF carries the same two fields as `properties.reachabilityState`/
`properties.impactAssessment`. JUnit does not carry the full object (a
structured node/edge object is a poor fit for JUnit's `<properties>`
text-value model) — but `--report-mode root-cause --format junit` does add
additive `rootCauseId`/`rootCause` attributes to each `<failure>` element,
without restructuring JUnit's per-symbol `<testcase>` tree; see
[Root-cause grouping](#root-cause-grouping) below.

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
