# ADR-044: Reachability-Aware Suppression and the Effective Public ABI

**Date:** 2026-07-17
**Status:** Accepted — P0 slice (this change) implemented: pipeline-order
correctness fix, `Suppression.reachability`/`allow_public_break`, entity/cause
namespace split, `suppression_would_hide_public_break` diagnostic. P1
(compose artifact break + L5 graph proof path into a first-class overlay
finding, propagation-aware edge semantics) and P2 (consumer-import evidence,
old-consumer/new-library execution harness) are roadmap, not committed to any
timeline — see "Roadmap" below.
**Decision maker:** Nikolay Petrov (@napetrov)

---

## Context

A field review of an oneDAL integration (PR 3693) found that a blanket
namespace suppression —

```yaml
suppressions:
  - namespace: "oneapi::dal::**::detail::**"
    reason: "Private implementation details"
```

— silently hid a genuine ABI break: a public inline function
(`oneapi::dal::train()`) called through to an exported `detail::`
specialization that the new library removed. Old applications compiled
against the public header fail to load against the new library — a real
`func_removed` break — but abicheck's report showed nothing, because the
suppression matched the internal symbol before the tool had a chance to
notice a public entry point depended on it.

The review's conclusion, and the premise of this ADR, is that this is **not
primarily a oneDAL configuration mistake** — a project cannot reasonably be
expected to hand-enumerate every internal symbol a public inline/template
function happens to reach — but **a tool correctness gap**: abicheck already
has the pieces to tell "truly unreachable internal churn" apart from
"internal implementation detail that is part of the effective public ABI
because public code depends on it," and does not consult them before
suppression runs.

### The pipeline-order bug, confirmed against the current code

`abicheck/post_processing.py`'s `DEFAULT_PIPELINE` (the sequence `compare()`
actually runs) has this shape today:

```text
...
FilterNonPublicSurface()
DemoteOffPythonSurface()
ApplySuppression()            # ← suppression removes raw evidence here
SuppressRenamedPairs()
FilterRedundant()
EnrichAffectedSymbols()
AttributeStdlibEmbedding()
DetectInternalLeaks()         # ← too late: the removed symbol's changes
                               #   were already filtered out by ApplySuppression
DemoteUnreachableInternalChurn()
...
```

`DetectInternalLeaks` (`internal_leak.detect_internal_leaks`) works by
scanning the **surviving** `changes` list for layout/identity-affecting
kinds (`_LEAK_TRIGGERING_KINDS`) whose root type is internal, then walking
the public surface to see if that type is reachable. If `ApplySuppression`
already removed the triggering `func_removed`/`type_*_changed` entries for
`detail::train_ops_dispatcher<...>` because they matched
`oneapi::dal::**::detail::**`, `DetectInternalLeaks` never sees them —
there is no evidence left to correlate with the public-reachability walk,
so `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` never fires, and
`DemoteUnreachableInternalChurn`'s "confirmed leak" check has nothing to
confirm. The class of change the review calls out —
`internal_symbol_required_by_public_api`, a **symbol-availability** leak
via `DECL_CALLS_DECL`, not a layout leak — is not even in
`_LEAK_TRIGGERING_KINDS` at all (see "What this ADR does not fix" below);
but the ordering bug independently defeats every leak kind the pipeline
*does* implement today, for any change matched by a broad rule that runs
before the detector sees it.

`ApplySuppression.run()` (and every step downstream of it that adds new
findings — `DetectCppPatterns`, `DetectNamespacePatterns`,
`DetectTemplatePatterns`, `DetectInternalLeaks` itself) hand-applies
`ctx.suppression.is_suppressed(c)` to its own new findings — a `# Synthetic
leak findings must respect user suppression rules too` comment in
`DetectInternalLeaks.run()` — but that only stops a *synthetic leak finding*
from surviving suppression; it does nothing to restore the raw evidence
`ApplySuppression` already deleted upstream, so the leak was never computed
in the first place.

### The second gap: entity namespace vs. cause namespace

`suppression.py`'s `namespace` selector (`_matches_namespace`) matches a
change if **any** of `change.symbol`, `change.caused_by_type`, or
`change.qualified_name` falls in the namespace:

```python
def _matches_namespace(compiled, change):
    return (
        _ns_match(compiled, change.symbol)
        or _ns_match(compiled, change.caused_by_type)
        or _ns_match(compiled, change.qualified_name)
    )
```

`caused_by_type` is set in two situations that must not be treated alike:

1. **Redundancy linking** (`diff_filtering._mark_as_redundant`): a *derived*
   change on a type is linked to its own root cause, both of which are the
   same entity — safe to match on either.
2. **Cross-entity attribution** (`internal_leak._build_leak_change`,
   `crosscheck.py`'s leak/dependency findings): a **public** symbol's finding
   carries `caused_by_type` pointing at the **internal** type responsible.
   `func_params_changed`/`var_type_changed` on a public function/variable
   whose signature changed because an internal root type changed
   (`diff_filtering._mark_as_redundant`, called from `_filter_redundant` for
   `_DERIVED_CHANGE_KINDS`) is exactly this shape: `symbol` is public,
   `caused_by_type` is the internal root.

A rule like `namespace: "oneapi::dal::**::detail::**"` matches case 2 via
`caused_by_type` alone — suppressing a finding whose `symbol` is the public,
breaking entity, purely because its documented *cause* happens to live in an
internal namespace. The suppression author who wrote the rule almost
certainly meant "hide churn *inside* `detail`," not "hide any public finding
whose explanation happens to mention `detail`" — the ADR's oneDAL example
(`kmeans::descriptor` vs. `kmeans::detail::descriptor_base`) is this exact
failure mode.

## The one rule that does not change

Same authority boundary this codebase has used since ADR-024 §D4/D5 and
restated in ADR-041: **suppression must never manufacture confidence it does
not have.** A suppression rule may remove noise; it may never be the reason
a real, public-reachable break goes unreported. Every mechanism this ADR
adds is a *safety default* on top of existing opt-in suppression syntax — no
existing narrowly-targeted suppression rule (`symbol`, `symbol_pattern` naming
one entity, `type_pattern`) changes behavior. Only the two broad selectors
(`namespace`, `source_location`) — the ones that can match an internal
symbol a suppression author never explicitly reasoned about — get a new
default, and it is an *opt-out* default (`reachability: any` restores the
old behavior for a rule the user has audited).

## Decision — P0 slice (this change)

### D1. Compute reachability before suppression runs, not after

New `PipelineStep`, `MarkReachability`, inserted into `DEFAULT_PIPELINE`
**before** `ApplySuppression`:

```text
FilterReservedFieldRenames … EnrichSourceLocations
FilterNonPublicSurface()
DemoteOffPythonSurface()
MarkReachability()            # ← new: tags every change, before suppression sees it
ApplySuppression()            # ← now reachability-aware (D2)
SuppressRenamedPairs()
FilterRedundant()
...
DetectInternalLeaks()         # unchanged position: the underlying evidence it
                               # needs is no longer gone by the time it runs,
                               # because MarkReachability ran first and a
                               # public-reachable change survives ApplySuppression
DemoteUnreachableInternalChurn()
...
```

This is deliberately **not** the full literal reordering the review
sketches (moving `FilterRedundant`/`DetectInternalLeaks` themselves ahead of
suppression). That reordering would break the invariant ADR-013 and ADR-004
established — "suppression runs before redundancy filtering, so a suppressed
change never contributes to the verdict whether root or derived" — and would
require every downstream step that already hand-applies
`ctx.suppression.is_suppressed()` to its own new findings
(`DetectCppPatterns`, `DetectNamespacePatterns`, `DetectTemplatePatterns`,
`DetectInternalLeaks`) to be re-audited for double-suppression or
under-suppression. The actual bug is narrower than "suppression runs at the
wrong pipeline position": it is "suppression has no reachability signal to
consult." Giving it that signal — computed once, up front, independent of
whatever else the pipeline does to the change list — fixes the reported
failure with a much smaller blast radius, and is the literal mechanism the
review's own "Recommended implementation" section describes
(`public_reachable: bool` metadata attached to each change before matching).

`MarkReachability.run()`:

- Calls `internal_leak.compute_leak_paths(ctx.old)` and
  `compute_leak_paths(ctx.new)` once — this is a pure function of the
  snapshot (function/variable/type declarations), not of the change list, so
  it is safe to compute before any filtering has happened and does not
  duplicate `DetectInternalLeaks`'s own later call (that call still needs to
  run after redundancy filtering to decide which *triggering* changes turn
  into a synthetic leak finding; this one only needs the raw reachable-type
  → path map).
- For each change, resolves its root type
  (`internal_leak._root_type_name_for_change`, the same helper
  `DetectInternalLeaks`/`DemoteUnreachableInternalChurn` already use) and
  looks it up in the merged old/new path map. When found, sets:
  - `Change.public_reachable = True`
  - `Change.reachability_kind` — `"value_embedding"` when
    `internal_leak._path_is_value_propagating` holds for at least one
    matched path, else `"pointer_or_signature"` — mirroring the
    `embedded_by_value` severity-hint distinction `_build_leak_change`
    already renders in prose, now available as structured metadata.
  - `Change.reachability_proof_path` — `internal_leak._format_path` of the
    shortest matched path, e.g.
    `"fn:oneapi::dal::train → base:oneapi::dal::detail::train_dispatch → oneapi::dal::kmeans::detail::train_ops_dispatcher<...>"`.
  A change whose root type is not internal, or is internal but unreachable
  from the public surface in either snapshot, keeps `public_reachable=False`
  and the two fields `None` — the common case, so this is a purely additive
  per-change annotation with no effect on a project with no internal-leak
  surface at all.

Three new fields on `Change` (`checker_types.py`), all defaulting to
`False`/`None` — same additive convention as `frozen_namespace_violation`
and `surface_exclusion_reason`, no schema/serialization version bump needed
since JSON/SARIF/JUnit reporters already round-trip `Change` via
`dataclasses.asdict`-style field enumeration.

**Post-merge review rounds (Codex + CI), same change:**

- **Perf regression.** The first-shipped `MarkReachability` ran
  `compute_leak_paths` unconditionally on every `compare()` call — CI's
  `benchmark_scaling.py` baseline-regression gate caught up to +5075% on
  type/struct-heavy scenarios, since this duplicated the identical walk
  `DetectInternalLeaks` already performs later, on every comparison, even
  when no suppression file is configured to ever consult the tag. Fixed by
  skipping the step entirely when `ctx.suppression is None` (mirroring
  `ApplySuppression`'s own no-op check) and, within a run, computing the
  leak-path walk lazily — only the first time a change whose subject is
  internal-namespaced is actually seen.
- **Pointer-only layout churn false-flagged.** `MarkReachability` originally
  marked *any* internal type reachable via *any* path (including a pure
  pointer/reference indirection) as `public_reachable`. But
  `DetectInternalLeaks` deliberately does **not** treat a pure-layout change
  reached only through a pointer as a leak (it is not consumer-visible), and
  `DemoteUnreachableInternalChurn` would still correctly demote such churn
  later — so tagging it reachable only refused a broad suppression rule and
  appended a spurious `suppression_would_hide_public_break` diagnostic for
  churn that was always going to be demoted anyway. Fixed by mirroring
  `DetectInternalLeaks`'s own `_IDENTITY_VTABLE_KINDS`/`_path_has_indirection`
  judgment inside `MarkReachability` before tagging.
- **Directly-public subjects are a known, deliberately unclosed gap —
  attempted, then reverted.** The internal-type-leak walk
  (`compute_leak_paths`) only ever records *internal* type names — it has no
  notion of "this change's own subject is already public." A broad
  `source_location`/`namespace` rule matching a genuinely public function
  purely by file path (e.g. a public function physically declared under a
  path a `source_location: "*/internal/*"` glob matches, with no
  internal-namespaced name at all) is therefore not protected by the
  reachability gate. A fix was attempted: broaden `MarkReachability` so any
  change whose subject is **not** internal-namespaced is marked
  `public_reachable = True` directly (no leak-path proof needed), with the
  leak-path walk only consulted for a subject that *is* internal-namespaced
  (also needing the internal-namespace check to widen to match what
  `Suppression._ns_match` checks at match time — `Change.qualified_name` and
  a demangled form of the raw symbol, since a mangled/`extern "C"` symbol
  reads as a single opaque segment otherwise). That fix also exposed a real
  `allow_public_break` scoping bug — the gate applied to every rule
  regardless of selector breadth, so an ordinary narrow `symbol:` waiver of a
  known removal suddenly needed `allow_public_break` too, regressing
  `test_suppression.py`'s basic suppression tests; corrected by scoping the
  gate to broad selectors only (D2 as written reflects this correction).

  The broadening itself was then reverted, one CI run later: it regressed
  `tests/test_libabigail_parity_extended.py::TestSuppressionParity::
  test_suppress_by_source_location` — a private helper (`internal_fn`, no
  namespace-segment hint) declared under `src/internal/helper.h`, matched and
  correctly suppressed by `source_location: "*/internal/*"`. Both that case
  and Codex's public-function example are, structurally, the **same shape**:
  an unqualified/non-namespaced `Visibility.PUBLIC` symbol under a path a
  `source_location` glob matches. `AbiSnapshot`'s visibility model marks
  *every* exported C/C++ symbol `Visibility.PUBLIC` regardless of whether the
  maintainer considers it part of the contract — that gap is the entire
  reason `source_location`-based suppression exists, to compensate for C/C++
  having no true "this is private" linkage visibility. No signal in the name
  or the snapshot distinguishes "genuinely public, accidentally path-matched"
  from "genuinely private, correctly path-matched," so no naming heuristic
  can close Codex's gap without also breaking the ordinary case. Reverted
  `MarkReachability` back to the leak-path-only computation; kept the
  `allow_public_break` broad-selector scoping (independently correct) and the
  pointer-only-layout fix above. Closing this gap for real needs actual
  dependency evidence — the L5 call-graph / consumer-import work already on
  the P1/P2 roadmap below — not a heuristic on the symbol's own spelling.

### D2. `Suppression` gains a reachability guard

New `Suppression` fields:

```yaml
- namespace: "oneapi::dal::**::detail::**"
  reachability: unreachable-only   # default for namespace / source_location
  reason: "Private implementation details"
```

- `reachability: "unreachable-only" | "any" | "public-only"`.
  - **Default** is selector-dependent, not a single global default: a rule
    is broad (defaults `"unreachable-only"`) when it has a broad,
    pattern-shaped selector (`namespace`/`entity_namespace`/
    `cause_namespace`/`source_location`) **and no primary narrow selector**
    (`symbol`/`symbol_pattern`/`type_pattern` — the mutually-exclusive trio
    the loader already treats as a rule's main selector). Otherwise it
    defaults `"any"` — unchanged behavior.
  - A primary narrow selector present alongside a broad one **exempts** the
    rule from "broad" (post-review correction, Codex): `symbol:
    "ns::detail::T", source_location: "*/internal/*"` already names the
    exact audited entity — the `source_location` addition can only
    *narrow* which changes on that one entity match (selectors combine with
    AND semantics), never introduce an unaudited match the bare `symbol:`
    selector wouldn't already have matched, so it keeps the narrow-selector
    "unchanged behavior" guarantee rather than suddenly requiring
    `allow_public_break`.
  - `member_name` is deliberately **not** a primary selector for this
    purpose: alone it matches a bare trailing name across *any* containing
    type/namespace (per its own docstring, "independent of the containing
    type"), so `namespace: "**::detail::**", member_name: "value_type"`
    still counts as broad — the namespace filter there is doing the real
    scoping work, not merely narrowing an already-pinned-down match. This
    is the one case the ADR's first-shipped, coarser "any broad selector
    present makes the whole rule broad" rule was actually protecting
    against; narrowing the rule to exempt only the primary trio preserves
    that protection while fixing the `symbol` + `source_location` case.
  - `"unreachable-only"`: the rule does not match a change with
    `public_reachable=True`.
  - `"any"`: no reachability filtering (today's behavior).
  - `"public-only"`: inverse — matches only `public_reachable=True` changes;
    the review's own "unusual, mainly debugging" case (e.g. temporarily
    silencing an in-progress leak investigation without touching genuinely
    private noise).
- `allow_public_break: bool = False`. When a **broad** rule would suppress a
  change that is both `public_reachable=True` **and** a member of
  `BREAKING_KINDS | API_BREAK_KINDS`, the match is refused — the change
  stays in the report — **unless** `allow_public_break: true` is set on that
  rule. This gate is scoped to broad selectors only, matching
  `reachability`'s own broad/narrow split (post-review correction — the
  first-shipped version applied it to every rule regardless of selector
  shape, which meant an ordinary, deliberate `symbol: "_ZN3foo..."` waiver of
  a known, intentional removal would *also* need `allow_public_break: true`
  the moment that symbol happened to read as public-reachable — defeating
  the basic "suppress one exact symbol I already reasoned about" use case
  suppression exists for in the first place; caught by `test_suppression.py`
  regressing when `MarkReachability` was broadened per D1's note below). A
  narrow rule (`symbol`/`symbol_pattern`/`type_pattern`/`member_name`) is
  exempt from this gate entirely — naming one exact symbol/type is already
  the deliberate, audited action, independent of whether that symbol turns
  out to be public or an internal type that leaks. A rule matching a
  non-breaking (`COMPATIBLE`/`RISK`) public-reachable change is also
  unaffected regardless of selector shape — this gate exists for exactly the
  failure mode the review reports (a `BREAKING` finding silently
  disappearing behind an unaudited glob), not to relitigate ordinary
  suppression of a `RISK` finding or of a symbol the author named exactly.
- A match refused by either gate is recorded (D4) rather than silently
  dropped, so a suppression author sees *why* their rule did not apply.

### D3. Split entity namespace from cause namespace

- `namespace` (kept as the primary spelling for backward compatibility) is
  now an explicit alias for a new canonical field, `entity_namespace`: it
  matches only `change.symbol` / `change.qualified_name` — **not**
  `change.caused_by_type`.
- New `cause_namespace` field: matches only `change.caused_by_type`, using
  the identical glob/ancestor-walk semantics `_ns_match` already implements.
- `entity_namespace` and `cause_namespace` may be combined on one rule
  (conjunctive, like every other selector pair) to express "suppress a
  finding on this internal entity *and* caused by this internal namespace" —
  the genuinely-safe case the old single `namespace` field conflated with
  the unsafe one.
- Loading both `namespace` and `entity_namespace` on the same rule is a
  load-time error (same "exactly one spelling" discipline `symbol`/
  `symbol_pattern`/`type_pattern` already enforce) — they are the same
  field under two names, not two independent selectors.

This is a **behavior change** to the pre-existing `namespace` field's
semantics (it no longer matches via `caused_by_type`), not merely an
addition. It is deliberately not shipped behind a compatibility flag: per
this repo's conventions (no backwards-compatibility shims for a correctness
fix), and because the old behavior is the review's headline false-negative
— a `namespace` rule that happens to over-match through `caused_by_type` was
never a feature anyone could have been relying on for a *correct* result, by
construction. `tests/test_frozen_namespace.py` had exactly one test asserting
the old via-`caused_by_type` match (`test_namespace_suppresses_caused_by_type_match`);
it is updated by this change to assert the new, safer behavior
(`test_namespace_does_not_match_caused_by_type`) plus a new counterpart test
for `cause_namespace` — the one place in this repo's own test suite that
depended on the old semantics is also the one place demonstrating exactly
why they were unsafe.

### D4. `suppression_would_hide_public_break` diagnostic

New `ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK` (`COMPATIBLE_WITH_RISK`
— it is not itself an ABI break; it is advisory metadata about a
suppression decision). Emitted by `ApplySuppression` for every
`(rule, change)` pair where a rule matched a change's selectors but the
match was refused by D2's reachability or `allow_public_break` gate:

```text
Namespace suppression "oneapi::dal::**::detail::**" matched
oneapi::dal::kmeans::detail::train_ops_dispatcher<...> but was not applied:
the symbol is public-reachable via oneapi::dal::train() →
oneapi::dal::detail::train_dispatch() →
oneapi::dal::kmeans::detail::train_ops_dispatcher<...>. Add
`allow_public_break: true` to this rule to suppress it anyway.
```

— the exact report shape the review's "Recommended implementation" section
asks for. This rides as an ordinary `Change` appended to the change list
(so it is visible in every existing report format — Markdown/JSON/SARIF/
JUnit — with no per-format plumbing), not a bolted-on side channel; a
project that wants CI to fail loudly when this fires can already do so via
`--severity-risk error` (existing severity-gating mechanism, ADR-009),
requiring no new CLI surface for this slice.

### What this ADR does not fix (roadmap, not committed)

The oneDAL dispatcher case (`func_removed` on an internal template
specialization reached only via `DECL_CALLS_DECL` from a public inline
function — no layout evidence, so `internal_leak.py`'s
`_LEAK_TRIGGERING_KINDS`/BFS-over-`RecordType` walk structurally cannot see
it) is **not** closed by this slice. `MarkReachability` reuses
`internal_leak.compute_leak_paths`, which only walks type-layout
reachability (inheritance, by-value fields, signatures) — it has no access
to the L5 semantic call graph (`source_graph.py`,
`buildsource/poi.resolve_changed_paths_public_impact`, ADR-041). Closing
that gap for real needs ADR-041 P1 item 3 ("public-entry impact closure...
nothing calls this one yet") wired into a scan/replay path, plus the new
`internal_symbol_required_by_public_api` overlay kind the review proposes.
That is a materially larger change — it needs a build (`compile_commands.json`)
or at minimum a header-only AST pass to have `DECL_CALLS_DECL` evidence at
all — and is **P1 roadmap**, below, not part of this P0 slice. This slice's
value is narrower but immediate: for every leak shape abicheck's existing
`internal_leak.py`/`crosscheck.py` detectors *can already* see (layout,
vtable, inheritance, embedded-by-value, and the type/field/base graph edges
`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` already covers), a suppression can no
longer make the detector blind to the evidence before it runs.

## Roadmap (not committed — scope/sequence per the usual planning process)

Numbering mirrors the review's own priority tiers.

### P1 — first-class detection

1. Wire `buildsource/poi.resolve_changed_paths_public_impact` (ADR-041 P1
   item 3's unwired pure helper) into the comparison pipeline so a
   `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL` path from a public entry to a
   changed/removed internal decl is available as reachability evidence
   alongside `MarkReachability`'s layout-only walk — closing the exact
   oneDAL dispatcher gap named above.
2. New overlay `ChangeKind`, `internal_symbol_required_by_public_api`:
   composition of an artifact-level `BREAKING_KINDS` finding (e.g.
   `func_removed`) with a public → internal `DECL_CALLS_DECL`/
   `DECL_REFERENCES_DECL` proof path — the call-graph analogue of
   `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API` (which is layout-only today). Per
   the authority rule this codebase has held since ADR-028 D3/ADR-041, the
   graph edge only *explains and correlates* an already-artifact-proven
   break; it never manufactures one on its own.
3. Propagation-aware edge semantics: distinguish layout-propagating edges
   (`TYPE_INHERITS`, by-value `TYPE_HAS_FIELD_TYPE`) from
   identity/signature-propagating edges (pointer/reference parameter or
   return of an internal type) from symbol-availability edges
   (`DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`) from indirection barriers
   (`pimpl<T>`, `unique_ptr<T>` — `internal_leak.py`'s
   `_is_known_pointer_wrapper`/`_field_is_indirect` already implement this
   distinction for the layout walk; extending the same taxonomy to the L5
   graph is the open part). `reachability_kind` (D1) is a two-value
   approximation of this; the full taxonomy is richer.
4. Surface `reachability_proof_path`/graph proof paths in every report
   format (currently `Change.description`-only, per ADR-041 P0 slice 3's
   convention) as first-class structured fields in JSON/SARIF.

### P2 — empirical validation

1. Consumer import manifests: `--consumer-binary`/`--consumer-dir`, ELF
   undefined-dynamic-symbol / PE-import / Mach-O-undefined-symbol
   collection from a baseline-built consumer, producing a
   `consumer_required_symbol_removed` finding when the candidate library no
   longer exports something a real consumer's baseline build referenced —
   ground truth that needs no template-dispatch understanding at all,
   independent of P1's static graph work.
2. Old-consumer/new-library execution harness (`LD_BIND_NOW=1`, optionally
   ASan/UBSan) as an opt-in validation capability alongside the static
   scanner, not a replacement for it.
3. New worked examples exercising this ADR's headline scenario end-to-end
   (public inline dispatch to an exported internal specialization; the same
   case under a blanket namespace suppression, asserting the break survives
   and the diagnostic fires; a safe pimpl counter-example) — the review's
   examples A/B/D are the most valuable regression coverage and are natural
   `examples/case*/` additions once P1 item 1 is wired, since case A/B need
   the call-graph reachability this P0 slice does not yet have.

## Consequences

- A suppression file with only narrow (`symbol`/`symbol_pattern`/
  `type_pattern`) rules sees **no behavior change** from D2/D4 — the
  reachability guard's default only engages for `namespace`/
  `source_location` rules.
- An existing broad `namespace`/`source_location` suppression rule that
  happens to also match public-reachable churn will, after this change,
  **stop suppressing** that subset of findings by default (they reappear in
  the report, tagged with the new diagnostic explaining why) — this is the
  intended fix, but is a visible behavior change for any suppression file
  relying on the old any-reachability default. `allow_public_break: true`
  is the escape hatch for a rule the maintainer has actually reviewed.
- `namespace`'s `caused_by_type` matching is removed outright (D3); a rule
  that depended on it (none found in this repo's own test suite/examples at
  authoring time) needs `cause_namespace` instead.
- No new CLI flags; no schema/serialization version bump. `SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`
  is a new `ChangeKind`, following the standard four-step procedure
  (`/CLAUDE.md` "Adding a new ChangeKind").

## References

- `abicheck/post_processing.py` — `DEFAULT_PIPELINE`, `MarkReachability`,
  `ApplySuppression`, `DetectInternalLeaks`, `DemoteUnreachableInternalChurn`
- `abicheck/internal_leak.py` — `compute_leak_paths`, `_LEAK_TRIGGERING_KINDS`,
  `_root_type_name_for_change`
- `abicheck/suppression.py` — `Suppression`, `SuppressionList`
- `abicheck/checker_types.py` — `Change`
- ADR-004 — Report filtering and deduplication (redundancy-before-verdict
  invariant this ADR deliberately does not disturb)
- ADR-013 — Suppression system design (pipeline-ordering rationale this ADR
  amends)
- ADR-024 — Public ABI surface resolution (audit-ledger / never-silently-drop
  convention this ADR follows for `suppression_would_hide_public_break`)
- ADR-041 — Compiler-facts semantic impact graph (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`,
  the unwired `poi.resolve_changed_paths_public_impact` P1 roadmap item this
  ADR's own P1 depends on)
