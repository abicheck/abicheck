# ADR-066: Longitudinal Compatibility History and Project-Defined Versioning Policy

**Date:** 2026-09-05
**Status:** Proposed — not implemented. Design record for the vision's
"history and versioning" decisions (`vision.md`); no code, schema, config
key, or default changes with this document. Implementation is sequenced in
[`plans/vision-api-abi-evolution.md`](../plans/vision-api-abi-evolution.md)
(workstream "Longitudinal history and versioning policy"), starting with
offline history over user-supplied snapshots. This ADR does not
reauthorize the baseline registry ADR-043 D4 retired, and it does not
introduce a hosted service.
**Decision maker:** maintainer (product decision recorded in `vision.md`);
technical sign-off pending review of this document.

## Context

Every abicheck entry point is two-sided: `compare`, `scan --against`, the
release fan-out, `deps compare`, and `aggregate` (which folds many reports
of *one* run). The release recommendation (`abicheck/semver.py`,
`recommend_release`) reads one `DiffResult` and maps verdict and change
kinds to a hard-coded strict-SemVer table (`MAJOR/MINOR/PATCH/NONE`, a
SONAME action, and an `actionable/review/unavailable` state). There is no
`versioning:` configuration key, no pre-1.0 rule, no calendar or
compatibility-line scheme, and no notion of a support or deprecation
window. Deprecation exists only as a per-pair transition
(`func/var/type/enum/field_deprecated_added|removed`, header-AST only),
never as a durable attribute with a lifecycle.

Baselines are identified by a `channel × target × profile` tuple whose
`project_ref` is an opaque label (a release tag or a git SHA) compared for
exact equality only (`abicheck/buildsource/baseline_set.py`); exactly one
baseline per tuple exists, with no ordering and no "previous N releases".
`AbiSnapshot` carries `version`, `git_commit`, `git_tag`, `created_at`, and
a `dump_provenance` block that stamps depth and frontend, not a content
digest or tool version; storage v2's `PackageManifest`/`VariantRef`
(ADR-062) carry declared-versus-captured variant coordinates but no
release label or predecessor pointer. Occurrence-preserving identity
(`EntityId`/`OccurrenceId`, `canonical_finding_id`) exists and is what a
history must key on; `EntityId.key` is not yet proven stable across
releases. A suppression has an expiry date but no version window.

So the questions the vision asks — when did an API first appear, when was
it deprecated or removed, which prior releases does a candidate still
promise support to, and does a removal satisfy the project's own rules —
have no owner, and the only versioning advice assumes a promise many
projects do not make.

## Decision

### D1 — History references immutable artifacts; it is not a second store

A history is an **index over snapshots the project already keeps**, not a
copy of their facts. Each history entry references one immutable snapshot
(or `ProjectSnapshot` package, ADR-062) by content digest and records:
project/component identity, release label, branch/channel/compatibility
line, variant/profile coordinates, evidence coverage, extraction/tool/
schema versions, the contract/config revision in force, and provenance
(who produced it, from what source ref). Entries are ordered by the
project's declared version scheme (D4) or by an explicit predecessor
relation; lexical tag order and upload time are never authoritative.

The first implementation is **offline**: a user supplies N existing
snapshots (two suffice), and the tool produces machine-readable lifecycle
events and coverage. No service, no network, no automatic fetching.
CI publication and resolution of history come later through the existing
baseline/artifact channels (ADR-047/059), and a baseline refresh is
storage activity, never an approval.

### D2 — Lifecycle events are per occurrence, per scope, and honest about gaps

Events (`first_observed`, `changed`, `deprecated`, `removed`, `reintroduced`)
are keyed by the existing entity/occurrence identity. Distinct overloads,
occurrences, ABI aliases, and template instances are never merged because
display names match; a rename with uncertain identity is a *possible
correspondence*, not continuity. Events are scoped to the variant/profile
and contract they were observed under — one platform's addition is not a
release-wide addition.

Terminology is bounded by coverage: `first_observed_in` is not
`introduced_in` unless every earlier entry in the relevant line was
captured with sufficient evidence; an unobserved deprecation is `unknown`,
not absent; a missing intermediate release yields an `unknown_interval`.
Branching histories and backports are kept as branches, never flattened.

### D3 — Adjacent pairs do not prove a window

Compatibility against a support window is evaluated against the baselines
the project actually promises (D4), not inferred transitively from adjacent
pass results — especially across differing contracts, profiles, or
evidence coverage. Cached pairwise results may be reused only under a
complete key (both digests, config fingerprint, tool version, identity
scheme) and each conclusion keeps the evidence it rests on.

### D4 — Versioning policy is a small, separable model

The project's versioning policy is resolved by the existing configuration
owner (ADR-049 D7's precedence: explicit request, run profile, project
config, built-in default) and consists of independent controls:

| Control | Meaning | Examples |
|---|---|---|
| `scheme` | How version labels parse and order | strict SemVer; SemVer with maintainer-defined relaxations; calendar; opaque labels with an explicit order; a named compatibility line |
| `promise` | What compatibility the project claims between two ordered versions | "binary-compatible within a major"; "source-compatible within a minor"; "no promise" |
| `support_window` | Which prior releases/lines a candidate must remain compatible with | last two minors; every release on the `5.x` line; an explicit list |
| `deprecation_window` | The minimum observed deprecation before a removal is policy-conforming | one minor; one release on the line; none |
| `enforcement` | What a policy deviation does | `warn` or `block`, per control |

Pre-1.0 behavior and branch-specific promises are stated explicitly in the
policy, never assumed. There is no built-in "weak SemVer": a relaxed policy
is whatever the project declares. The built-in default is the current
behavior (strict SemVer advice, no windows, advisory only), so no existing
run changes.

### D5 — Policy changes acceptance; it never changes facts

Whether a release is *accepted* under its versioning policy is a distinct
decision from whether a change is *compatible*. A major-version bump can
make a known break policy-conforming; the break stays `BREAKING`, stays in
the report, and still drives the SONAME advice. A relaxed policy may warn
instead of block; it cannot remove findings, inflate evidence, or alter a
verdict. Changing `enforcement` or `scheme` cannot change the raw finding
set or any evidence status — an executable invariant, not prose.

Advice distinguishes three things: an *observed recommendation* (what the
delta implies), an *unmet release policy* (what the project's own rules
say), and *insufficient evidence for advice* (the existing `unavailable`
state). SONAME advice remains conditional on platform and contract.

### D6 — History integrates with acknowledgment; it does not invent approval

A history entry may link to an acknowledgment record (ADR-067) by its id;
history never carries a separate approval mechanism. Re-evaluating a
recorded transition under a later policy is a *new* result that references
the original run's decisions and config fingerprint; it never rewrites the
original acceptance.

### D7 — Reporting through the canonical document

Timelines and lifecycle projections are sections of the canonical report
document (ADR-036/061), derived from recorded events; a diagram is a view
of those events, not evidence. Optional bounded retention (how many entries
a project keeps) is a storage setting, not a semantic one.

## Consequences

- No pairwise run changes: with no history and no policy, the release
  recommendation is exactly today's.
- `abicheck/semver.py` becomes the *observed recommendation* half of a
  two-part answer; the policy evaluation is a new `policy/` owner that
  reads the resolved versioning policy and the (optional) history.
- `versioning:` is a new `.abicheck.yml` namespace registered with the
  config reference generator and the docs topic registry in the same
  slice that implements it — not before.
- Suppressions gain nothing here; version-window scoping of an
  acknowledgment is ADR-067's field, referenced by history, not duplicated.

## Relationship to existing decisions

Extends ADR-015/059/062 (snapshot identity, compression, storage v2),
ADR-047 (baseline lifecycle and publication channels), ADR-049 (config
precedence, contract revision), ADR-055 (typed requests), ADR-063
(identity, `RunOutcome`), ADR-064 (exit precedence: `enforcement: block`
folds through the existing gate/policy axis, never a new scheme). Replaces
nothing. ADR-022's registry stays retired; a history index is a file a
project owns, not a service abicheck runs.

## Implementation slices

S0: this ADR, model trade-offs on real fixtures, migration/retention
design. S1: offline history over user-supplied snapshots, emitting
machine-readable events and coverage. S2: the versioning policy model,
support/deprecation evaluation, and integration with the existing
SemVer/SONAME advice. S3: CI publication/resolution through the existing
artifact channels. S4: report projections, bounded retention, and
reproducible comparison reuse.

## Mandatory tests (contract)

Three-release add/deprecate/remove sequences; strict versus relaxed policy
on the **same** raw delta (identical findings and evidence, different
acceptance); non-SemVer labels; backports and release branches; a missing
intermediate release; lost debug or header evidence in one entry; a symbol
removed and reintroduced; an API present in one variant only; an unknown
deprecation date; rebased source paths that must not reset identity;
ambiguous renames that must not be asserted; re-evaluation under a new
policy without overwriting the original result. Memory/time budgets are set
from measured fixtures in S1, not promised here.
