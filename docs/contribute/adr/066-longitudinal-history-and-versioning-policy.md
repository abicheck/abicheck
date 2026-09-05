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
window. Deprecation is already a persisted per-declaration fact
(`deprecated`/`deprecated_fact` on functions, variables, records, and
enums, snapshot schema v40) with per-pair transition kinds
(`func/var/type/enum/field_deprecated_added|removed`, header-AST only)
derived from it; what is missing is any *lifecycle* read of that fact
across more than two releases.

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
(who produced it, from what source ref). Entries are ordered by an explicit predecessor relation the project
supplies, or, when none is supplied, by an order derived once from the
declared version scheme (D4) *at index-build time*. Either way the
resulting release order/predecessor graph is **persisted with the index
as input provenance and frozen**: lifecycle events are computed from that
persisted order, and a later policy re-evaluation (D6) reads it back and
never re-derives it. A different `scheme` therefore cannot reorder the
same entries or change an observed `first_observed`/`removed`/
`reintroduced` event; changing the order is an explicit rebuild of the
index that produces a new history with its own provenance, not a
re-evaluation. Lexical tag order and upload time are never authoritative.

The first implementation is **offline**: a user supplies N existing
snapshots (two suffice), and the tool produces machine-readable lifecycle
events and coverage. No service, no network, no automatic fetching.
CI publication and resolution of history come later through the existing
baseline/artifact channels (ADR-047/059), and a baseline refresh is
storage activity, never an approval.

### D2 — Lifecycle events are per occurrence, per scope, and honest about gaps

Events (`first_observed`, `changed`, `deprecated`, `removed`, `reintroduced`)
are keyed by the existing entity identity (`EntityId`), with occurrence
disambiguation carried through an explicit **cross-release correspondence
step** rather than by using today's `OccurrenceId` directly: the
multi-TU normalizer builds an occurrence's disambiguator from the TU name
and source location (`abicheck/extract/manifest_semantic_ir.py`), so a
rebased or relocated but otherwise unchanged source tree would yield a new
key and read as removal-plus-reintroduction. The correspondence step
matches occurrences across entries on a normalized, persistent occurrence
key built only from change-stable material: an **identity projection** of
`EntityId` (kind, scope path, name — deliberately omitting `EntityId.extra`,
which for a function carries the mangled name or the normalized signature
discriminator, `abicheck/model/identity.py`'s `entity_id_for_function`, and
so changes with a parameter change), plus TU-relative and root-relative
paths and declaration anchors. Never mutable semantic payload —
`CanonicalEntity.canonical_spelling`, a signature, a mangled name, an
underlying type, a constant value — since those change exactly when a
`changed` event should be emitted and would otherwise read as
removal-plus-reintroduction; payload is compared only after
correspondence is established. Overloads, which the projection no longer
separates, are disambiguated inside the correspondence step by their
signature discriminators: a one-to-one signature match is continuity;
anything else — including a changed signature with a single remaining
candidate, since old `f(int)`/new `f(double)` look identical whether one
declaration changed or `f(int)` was removed while an unrelated overload
was added — is a *possible correspondence* only, never an asserted
`changed` event. A possible correspondence becomes `changed` only when
stable provenance corroborates it (the same declaration anchor in the
same TU-relative location, or a project-supplied explicit rename/
signature-change mapping); otherwise history records a `removed` and a
`first_observed` pair with the correspondence attached as a hint, so
deprecation or first-observed history is never carried between distinct
APIs. It reports an ambiguous match as
a *possible correspondence*, and never asserts continuity it cannot prove.
Defining that key is S1's first deliverable, gated by the rebased-path
test below. Distinct overloads, occurrences, ABI aliases, and template
instances are never merged because display names match; a rename with
uncertain identity is a *possible correspondence*, not continuity. Events are scoped to the variant/profile
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
owner, ADR-049 D7's complete precedence as `abicheck/
compatibility_evaluation_resolver.py` implements it: explicit CLI/API
request > legacy alias > run recipe > run profile > project config >
built-in default. Versioning controls are *semantic* fields, not execution
fields, so the run-profile tier is **not eligible** for them (the resolver
rejects a run-profile candidate for any field that has not opted in as an
execution field); a run recipe may supply them. The policy consists of
independent controls:

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
set, any evidence status, or — because the release order is frozen
provenance (D1) — any recorded lifecycle event; an executable invariant,
not prose.

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
