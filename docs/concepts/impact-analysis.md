# Unified Impact Assessment

abicheck's reachability, graph-proof-path, and suppression-decision signals
for a single finding used to live in several independently-set fields on
that finding, with no shared object to query them through. This page
documents `impact_assessment` — the unified, queryable view over those
fields — and `reachability_state`, the tri-state reachability signal it made
visible in JSON/SARIF output for the first time.

This is slice 1 of [G29 Phase 3](../development/plans/g29-impact-analysis-layer.md)
([ADR-051](../development/adr/051-unified-impact-assessment-model.md)). It
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
  ([ADR-048](../development/adr/048-canonical-entity-identity-and-graph-reconciliation.md)),
  `prose` is the human-readable rendering. `steps` is empty when only the
  prose rendering is available.
- `decision` records whether the finding was kept or suppressed, and (when a
  [pattern-aware modulation](../user-guide/api-surface-intelligence.md) or
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
`properties.impactAssessment`. JUnit does not — see ADR-051 / ADR-048 for
why.

## What this does not cover yet

`impact_assessment` does not (yet) include which consumers or use cases are
affected, a coverage summary, or a root-cause identifier — those need the
consumer/use-case graph (G29 Phase 4), the per-role coverage matrix wired
through the impact layer, and the root-cause correlator (G29 Phase 6),
none of which exist yet. Adding empty placeholder fields for data no
producer can populate would misrepresent what abicheck actually knows, so
they are left out of the schema entirely rather than always-`null`. See
[ADR-051](../development/adr/051-unified-impact-assessment-model.md) for the
full list of what this slice deliberately does not implement.
