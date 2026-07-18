# ADR-044: Reachability-Aware Suppression and the Effective Public ABI

**Date:** 2026-07-17
**Status:** Accepted — P0 slice implemented: pipeline-order correctness fix,
`Suppression.reachability`/`allow_public_break`, entity/cause namespace
split, `suppression_would_hide_public_break` diagnostic. **P1 (first-class
detection) is now also implemented** — see "P1 slice" below: L5 call-graph
evidence wired into `MarkReachability`/`DetectInternalLeaks`, the new
`internal_symbol_required_by_public_api` overlay `ChangeKind`, a third
`reachability_kind` value (`symbol_availability`), structured JSON/SARIF
reachability fields, `PolicyFile.internal_namespaces`, and the two remaining
`checker.py` suppression call sites routed through the diagnostic-emitting
helper. P2 (consumer-import evidence, old-consumer/new-library execution
harness, new worked examples) remains roadmap, not committed to any
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

- **Skip the walk for narrow-only suppression files too, not just no
  suppression at all (Codex).** The `ctx.suppression is None` skip above
  only covers the *no suppression configured* case — but a suppression file
  containing only narrow rules (`symbol`/`symbol_pattern`/`type_pattern`,
  the common case: a handful of exact waivers) with the default (or
  explicit `"any"`) `reachability` is *also* provably indifferent to the
  tag: both `_passes_reachability_gate` (short-circuits on
  `resolved == "any"`) and `_passes_public_break_gate` (short-circuits on
  `not self._is_broad_selector`) return without ever reading
  `Change.public_reachable` for such a rule. Running the public-surface
  walk for that file is exactly the same waste the `ctx.suppression is
  None` fix targets. Added `SuppressionList.needs_reachability_evidence()`
  — true iff at least one rule is broad or has an explicit non-`"any"`
  `reachability` — and gated `MarkReachability` on it alongside the
  existing `None` check.

- **A third late-detector synthetic-finding gap, this time for genuinely
  public (not internal-leak) findings (Codex).** The two already-fixed
  cases (`internal_leak._build_leak_change`,
  `diff_templates._leak_change`) cover findings whose subject is an
  *internal* type reached via a public entry point. `diff_namespaces.py`'s
  `DetectNamespacePatterns` — also running after `ApplySuppression` — has a
  different shape: `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`/
  `EXPERIMENTAL_GRADUATED` (function path) and `STD_REEXPORT_REMOVED` build
  fresh `Change`s for a subject that is *itself* public (an `experimental::`/
  re-exported function graduating or vanishing), not merely reachable from
  one. Untagged, a broad `namespace: "lib::experimental::*"` rule's default
  `unreachable-only` reachability silently suppressed the API-break finding
  with no diagnostic — the same failure mode this ADR exists to close, one
  detector later than the two already-fixed cases. Fixed the same way:
  tagged `public_reachable=True`/`reachability_kind="direct_public_symbol"`
  at construction time in `_emit_experimental_change`/
  `_build_std_reexport_change` — but **only** for the function-sourced path.
  `_index_funcs_by_stable_key`/`detect_std_reexport_removed` filter on
  `Visibility.PUBLIC` before ever building a `Change`, so those findings'
  mere existence already proves the subject is public — the same reliable
  signal the two already-fixed cases have. The *type*-sourced path
  (`_index_types_by_stable_key`) has no such signal: `RecordType` carries no
  visibility field at all (unlike `Function`/`Variable`), and that index
  walks every type in `snap.types` regardless of whether it is genuinely
  public or an internal type that merely happens to have an
  "experimental"-segment name — tagging it too would reintroduce exactly the
  unreliable-heuristic problem that got the broader `MarkReachability`
  broadening reverted earlier in this same review cycle, just via a
  different code path. `_emit_experimental_change`/`_findings_for` gained an
  explicit `subject_is_public` parameter so the two call sites (funcs vs.
  types) state their own reliability instead of the function silently
  assuming one for both. Unlike a raw pre-existing change (suppressed via
  `ApplySuppression`, which can attach `suppression_would_hide_public_break`),
  these late-detector findings suppress inline via their own
  `ctx.suppression.is_suppressed(c)` call and have no diagnostic path — the
  same established scope boundary the two already-fixed cases also have;
  not being silently suppressed is the fix, a diagnostic for this whole
  class of finding is a separate, pre-existing gap this change does not
  newly introduce or attempt to close.

- **A fourth late-detector sweep, this time the whole `diff_templates.py`
  module (Codex).** Fresh evidence beyond the namespace-detector fix above:
  `DetectTemplatePatterns` (also running after `ApplySuppression`) has the
  identical gap for `CPO_KIND_CHANGED` — a public name flipping between
  function and CPO-variable form. Rather than fix that one kind and wait for
  a further round to find its siblings, audited every detector
  `detect_template_patterns` calls: `CPO_KIND_CHANGED`,
  `OVERLOAD_SET_REROUTED`, and `UNSPECIFIED_RETURN_NOW_NAMED` all filter
  their source snapshot walk to `Visibility.PUBLIC` before ever building a
  `Change`, so all three got the same construction-time
  `public_reachable=True`/`reachability_kind="direct_public_symbol"` tag as
  the namespace-detector fix. `MANDATORY_TEMPLATE_PARAM_ADDED` was
  deliberately left **untagged** — its arity index merges observations from
  both public functions *and* `snap.types` under one shared stem key with no
  way to tell which contributed a given finding, the same
  no-reliable-signal problem the type-sourced namespace-detector path has;
  tagging it would reintroduce the reverted heuristic bug one level deeper.
  Also swept `detect_missing_instantiations` (`INSTANTIATION_MISSING_FROM_BINARY`,
  runs via `DetectCppPatterns`, same after-`ApplySuppression` position,
  same `Visibility.PUBLIC`-filtered construction) even though Codex's report
  didn't name it, since it is the same reliable-signal shape found while
  already auditing the module. A broader sweep of `diff_cpp_patterns.py`'s
  remaining detectors (`SYCL_OVERLOAD_SET_REMOVED`,
  `CPU_DISPATCH_ISA_DROPPED`, `TAG_TYPE_RENAMED`,
  `DEFAULT_TEMPLATE_ARG_CHANGED`, `INLINE_BODY_REFERENCES_RENAMED_MEMBER`,
  `BUNDLE_SONAME_SKEW` — several `BREAKING`) for the same pattern remains
  open; scoped out of this round to avoid rushing verification of six more
  detectors across two large files without individually confirming each
  one's visibility-filtering the way every fix above required.
- **A fifth late-detector gap, back in `diff_namespaces.py` itself
  (Codex).** `detect_namespace_patterns()` also runs
  `detect_inline_namespace_version_bump`, which was missed by the third
  round's sweep of that same module (that round covered
  `_emit_experimental_change`/`_build_std_reexport_change` only). It builds
  `INLINE_NAMESPACE_VERSION_BUMPED` from `_emit_version_bumps`, which reads
  `old_list[0]`/`new_list[0]` out of an index keyed by version-stripped
  namespace segments — and that index's entries come from
  `_collect_versioned_entries`, which merges public-function-sourced (`f.name`
  filtered to `Visibility.PUBLIC`) and type-sourced (`t.name`, unfiltered —
  `RecordType` has no visibility field) observations into one list per key,
  same shape as `MANDATORY_TEMPLATE_PARAM_ADDED`'s arity index. The
  difference here: each entry is a `(qualified_name, version, kind)` tuple
  that already carries which source it came from, so — unlike the arity
  index — the signal survives into `_emit_version_bumps` and just wasn't
  read. Fixed by checking `old_list[0][2] == "function" and new_list[0][2]
  == "function"` (both sides, since `old_q`/`new_q` both flow into the
  emitted `Change`) before tagging `public_reachable=True`/
  `reachability_kind="direct_public_symbol"` — a type-sourced bump stays
  untagged for the same no-visibility-field reason as the arity index.
- **The `diff_cpp_patterns.py` sweep the fourth round deliberately
  deferred (Codex).** Fresh evidence named `TAG_TYPE_RENAMED` specifically:
  `detect_tag_type_renamed` builds its `Change` from a *type* pairing, but
  gates the finding on symbol evidence (`only_removed`/`only_added`)
  explicitly scoped to `_PUBLIC_VIS` per its own docstring — the finding
  only exists when real public-surface mangled symbols embed the tag's
  leaf name, the same "finding's mere existence already proves public
  reachability" signal the earlier leak-finding and namespace/template
  fixes rely on. Rather than fix only the named kind, finished the sweep
  the fourth round scoped out: `detect_sycl_overload_set_removal`
  (`SYCL_OVERLOAD_SET_REMOVED`) and `detect_cpu_dispatch_isa_dropped`
  (`CPU_DISPATCH_ISA_DROPPED`) both build their grouped findings
  exclusively from `_PUBLIC_VIS`-filtered `old_funcs`/`new_funcs` (plus, for
  the ISA detector, the raw PE/Mach-O export table — public by
  definition), so both got the same construction-time tag.
  `detect_default_template_arg_changed` (`DEFAULT_TEMPLATE_ARG_CHANGED`)
  is the same shape (`old_funcs`/`new_funcs` scoped to `_PUBLIC_VIS`), also
  tagged. `detect_inline_body_renamed_member`
  (`INLINE_BODY_REFERENCES_RENAMED_MEMBER`) was audited and deliberately
  left **untagged**: its `_find_public_pimpl_holders` helper infers
  "public" from `not is_internal_type(name)` — a naming/namespace
  heuristic, not a `Visibility.PUBLIC` filter — the exact shape of the
  heuristic that was tried and reverted earlier in this cycle (see the
  D1 "directly-public subjects" entry above); tagging it here would
  reintroduce that reverted bug through a different detector.
  `detect_bundle_soname_skew` (`BUNDLE_SONAME_SKEW`) turned out to be a
  false alarm on the original P2 list: it is invoked from `bundle.py`'s
  separate `compare-release`/bundle-cohort command, never from
  `DetectCppPatterns` or any path that runs through `MarkReachability`/
  `ApplySuppression` at all, so the pipeline-order bug this ADR closes
  does not apply to it.
- **Self-review: `entity_namespace` missing from the D4 diagnostic's own
  selector display.** With CI green and Codex quiet, an independent
  self-review pass (prompted by "is this a full implementation, what's
  left") re-read the diff cold rather than re-trusting the prior rounds'
  conclusions, and found `_build_suppression_overreach_change`
  (`post_processing.py`) still fell back through `rule.namespace or
  rule.cause_namespace or rule.source_location or rule.symbol or
  rule.symbol_pattern or rule.type_pattern or "?"` — `entity_namespace`,
  the canonical spelling introduced by D3's namespace/cause split, was
  never added to this chain, even though the equivalent string-building in
  `SuppressionAudit` (`suppression.py`) already includes it. A rule written
  with `entity_namespace:` (not the legacy `namespace:` alias) that
  triggers `suppression_would_hide_public_break` would render as `"?"` (or
  whichever unrelated field happened to be set) in the diagnostic instead
  of naming the actual rule — undermining D4's whole stated purpose of
  "explaining why and how to override it." No test caught this: the
  existing regression test used the `namespace` alias and asserted only
  that `"allow_public_break"` appeared in the message, never the selector
  text itself. Also noticed while fixing it: `rule.symbol`/
  `rule.symbol_pattern`/`rule.type_pattern` in that same fallback chain are
  unreachable dead code — `would_withhold()` requires
  `not self._passes_public_break_gate(change)`, and that gate returns
  `True` unconditionally whenever `_is_broad_selector` is `False`, which is
  exactly the case whenever any of those three (primary narrow selectors)
  is set — so a rule naming one can never reach this diagnostic at all.
  Fixed by adding `rule.entity_namespace` to the chain and dropping the
  three dead branches (only the four broad-shaped fields — `namespace`,
  `entity_namespace`, `cause_namespace`, `source_location` — can ever
  actually appear here), plus a new regression test using `entity_namespace`
  only and asserting the rendered selector text, not just a substring of
  the fixed suffix.
- **`DEFAULT_INTERNAL_NAMESPACES` is a hard-coded convention list; a
  project using a different one is invisible to `MarkReachability`
  (Codex, P2).** `MarkReachability` called `compute_leak_paths(ctx.old/new,
  DEFAULT_INTERNAL_NAMESPACES)` with the walk's own hard-coded default
  (`detail`/`impl`/`internal`/`__detail`/`_impl`) with no way to override
  it. A project whose internal-implementation convention uses a different
  segment — Codex's example: `ns::priv::*` — is never recognized as
  "internal" by the walk at all, so a change on a type in that namespace
  never gets `public_reachable` tagged, regardless of whether it is
  genuinely reachable from a public type. A broad `namespace: "ns::priv::*"`
  suppression rule (default `reachability="unreachable-only"`) then
  suppresses the change with **no diagnostic** — exactly the failure mode
  this ADR exists to close, just for any internal-namespace convention
  outside the default five tokens. Verified this is not a heuristic gap
  like the reverted D1 "directly-public subjects" fix above — sibling
  pipeline steps `DetectInternalLeaks` and `DemoteUnreachableInternalChurn`
  (both pre-dating this ADR) already accept a `namespaces: tuple[str, ...]
  | None` constructor override for exactly this reason; `MarkReachability`
  was simply the odd one out, hard-coding the default with no override
  hook at all. Fixed by giving `MarkReachability` the identical constructor
  parameter, so it is at least structurally consistent with its siblings.
  This does **not** fully close the gap: `DEFAULT_PIPELINE` still
  constructs all three steps with no arguments (confirmed — no caller
  anywhere threads a non-default value today), so every project is still
  limited to the same five-token default until a real configuration
  surface exists. Deliberately did not attempt to auto-derive "the"
  internal segment from a suppression rule's own namespace glob (e.g.
  extracting literal segments from `"ns::priv::*"`) — a pattern's leading
  segments are often shared with unrelated *public* types (e.g.
  `"oneapi::dal::**::priv::**"` — "oneapi"/"dal" are not internal markers),
  so blindly harvesting them would misclassify public types as internal
  project-wide, the same unreliable-heuristic failure mode as the reverted
  D1 fix, just reached from the opposite direction. Closing this for real
  needs a genuine project-level configuration surface (e.g. a
  `PolicyFile.internal_namespaces:` key) threaded consistently through
  `MarkReachability`/`DetectInternalLeaks`/`DemoteUnreachableInternalChurn`/
  `DetectNamespacePatterns` — added to the P1 roadmap below as a concrete,
  scoped follow-up rather than attempted reactively in this round.
- **A sixth late-detector gap, this time entirely outside
  `post_processing.py` (Codex).** Fresh evidence: `pattern_verdicts.
  apply_pattern_verdicts()` — invoked from `checker._apply_pattern_verdicts_step`,
  well after `post_processing.DEFAULT_PIPELINE` (and thus `MarkReachability`/
  `ApplySuppression`) has already run — appends new `OPAQUE_INVARIANT_BROKEN`/
  `HANDLE_TYPE_CHANGED` `Change`s that `checker._filter_pattern_synthetic`
  then runs through its own `suppression.is_suppressed(c)` call, the same
  "late synthetic finding, no diagnostic path" shape as the `diff_namespaces.py`/
  `diff_templates.py`/`diff_cpp_patterns.py` sweeps above, just reached from a
  completely different module (`--pattern-verdicts`, ADR-027, not part of the
  `DEFAULT_PIPELINE` steps this ADR had audited). Audited both kinds:
  `OPAQUE_INVARIANT_BROKEN`'s subject type is only ever tagged `OPAQUE_POINTER`
  in `old_idioms` (a precondition for this finding) when `idioms.
  _recognise_opaque`/`_public_pointer_only` found a genuine `Visibility.PUBLIC`
  function referencing it — the same reliable signal the other
  `Visibility.PUBLIC`-filtered late-detector findings have — so tagged
  `public_reachable=True`/`reachability_kind="direct_public_symbol"` at
  construction. `HANDLE_TYPE_CHANGED`'s subject is a typedef alias:
  `AbiSnapshot.typedefs` is a plain `dict[str, str]` with no visibility
  field at all (typedefs, unlike `Function`/`Variable`, carry none), so
  `_recognise_handle` walking every declared typedef gives no reliable
  public/private signal for the alias itself — deliberately left untagged,
  same reasoning as `MANDATORY_TEMPLATE_PARAM_ADDED`. Added regression
  assertions for both (including the deliberately-untagged case) to
  `test_pattern_verdicts.py`. A wider audit of whether any *other*
  ADR-027/pattern-verdict-adjacent modules construct late synthetic findings
  the same way remains open — this round only confirmed the two kinds
  Codex's fresh evidence named.
- **`RecordType.origin` was a real, overlooked signal — closes three
  "deliberately untagged" cases from earlier rounds (Codex).** Every prior
  round asserted "`RecordType` carries no visibility field, so a
  type-sourced finding has no reliable public/internal signal" —
  `MANDATORY_TEMPLATE_PARAM_ADDED`, the type-sourced path of
  `_emit_experimental_change`/`EXPERIMENTAL_GRADUATED`/
  `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`, and the type-sourced path of
  `_emit_version_bumps`/`INLINE_NAMESPACE_VERSION_BUMPED` were all left
  untagged on that basis. That premise was incomplete: `RecordType` does
  carry `origin: ScopeOrigin` (ADR-024 D1's Linkage × Origin surface
  model), and `ScopeOrigin.PUBLIC_HEADER` — set only under ADR-024's opt-in
  `--public-header`/`--public-header-dir` scoping — is exactly the reliable
  signal these three sites were missing. Without that flag every type's
  `origin` is `ScopeOrigin.UNKNOWN` (per `ScopeOrigin`'s own docstring),
  so this degrades to the prior untagged behavior automatically for the
  common (no public-header set) case — not a regression, purely additive.
  Fixed all three:
  - `diff_namespaces._emit_experimental_change`/`_findings_for`: replaced
    the static `subject_is_public: bool` parameter with `old_origins`/
    `new_origins` maps (`None` for the always-public function path,
    `{qualified_name: ScopeOrigin}` for the type path), looked up per
    finding against the specific `old_q`/`new_q` subject.
  - `diff_namespaces._emit_version_bumps`/`_collect_versioned_entries`:
    the per-entry `"function"|"type"` string became a plain `is_public: bool`
    (`True` for a `Visibility.PUBLIC` function, `origin ==
    ScopeOrigin.PUBLIC_HEADER` for a type) — no other caller read the old
    string value.
  - `diff_templates.detect_mandatory_template_param_added`/`_arities`: now
    returns a second `{stem: bool}` map alongside the arity-set map,
    `True` when *any* contributing observation for that stem (function or
    type) was reliably public. Deliberately "any observation" rather than
    "the specific min-arity-driving one" — a stem with genuine public
    evidence should not be treated as safe-to-hide by a broad suppression
    rule even if a sibling internal instantiation also happened to share
    the stem name; this stays conservative in the direction this ADR
    cares about (never *hides* a real public break), unlike the reverted
    D1 heuristic which risked the opposite (falsely claiming public
    reachability with zero real evidence).
  Added `_rec_public()`/`ScopeOrigin.PUBLIC_HEADER` regression tests
  alongside each existing `ScopeOrigin.UNKNOWN`-default case in
  `test_diff_namespaces.py`/`test_diff_templates.py`. Left
  `INLINE_BODY_REFERENCES_RENAMED_MEMBER` (`diff_cpp_patterns.py`)
  untouched — its untagged reasoning is a different shape (a namespace
  heuristic risking *false* public claims, not a missing origin signal),
  not something `ScopeOrigin` fixes.
- **Self-review follow-up on the `RecordType.origin` fix above (two minor
  findings).** `_emit_experimental_change`/`_findings_for`'s new
  `old_origins`/`new_origins` parameters were typed `dict[str, object] |
  None` — loose enough to accept any value type and lose the point of
  adding a typed lookup in the first place; narrowed to `dict[str,
  ScopeOrigin] | None`. Separately, `detect_experimental_namespace_changes`
  built those maps with a plain `{t.name: t.origin for t in old.types}`
  comprehension, which silently lets a later `RecordType` sharing an exact
  qualified name overwrite an earlier one's origin — inconsistent with
  `pattern_verdicts._exact_record`'s established "first match wins"
  exact-identity convention elsewhere in the reachability code. Replaced
  with a new `_origin_by_name()` helper using `dict.setdefault` for
  first-occurrence-wins semantics, with a docstring citing the
  `_exact_record` precedent. Two duplicate-named `RecordType`s in one
  snapshot is unusual input either way; this is a consistency fix, not a
  response to an observed bug. Added `TestOriginByName` regression tests
  (`test_simple_lookup`, `test_duplicate_name_first_occurrence_wins`) to
  `test_diff_namespaces.py`.
- **`_emit_version_bumps` required BOTH sides public-header-tagged, silently
  hiding an asymmetric old-consumer break (Codex, fresh evidence).**
  `subject_is_public = old_list[0][2] and new_list[0][2]` meant a type
  version bump (`ns::__1::queue` → `ns::__2::queue`) stayed untagged
  whenever only one side carried `ScopeOrigin.PUBLIC_HEADER` evidence — e.g.
  the type moved out of the scoped public-header set, or `--public-header`
  scoping only covered one snapshot. But the old side alone already proves
  the break: an application linked against the old public symbol breaks
  regardless of whether the new symbol also has public-header evidence —
  the same "old-side-only" reasoning `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`
  already uses (checks only `old_origins`, D1 above). Changed `and` to `or` —
  either side's public-header evidence is now sufficient, matching
  `MANDATORY_TEMPLATE_PARAM_ADDED`'s "any observation" conservatism (stay
  tagged reachable when *any* reliable evidence exists, never require all of
  it). Added `test_old_side_public_alone_is_reachable`/
  `test_new_side_public_alone_is_reachable` to `test_diff_namespaces.py`.
- **Late detectors dropped the withheld-rule diagnostic even after their
  findings were correctly kept (Codex, fresh evidence).** `DetectCppPatterns`,
  `DetectTemplatePatterns`, and `DetectNamespacePatterns` each build fresh
  `Change` objects *after* `ApplySuppression` already ran, so they filter
  their own findings through suppression by hand — but did so via the plain
  `SuppressionList.is_suppressed()` boolean, which silently discards the
  "matched but withheld by the reachability gate" information
  `SuppressionList.evaluate()` reports. The finding stayed correctly kept
  (not suppressed — that part of the D1/D3 fixes above was never wrong), but
  the `SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK` diagnostic `ApplySuppression`
  would have produced for the same rule never appeared, leaving users with
  no explanation of why their matching rule didn't apply. Audited every
  post-`ApplySuppression` step with the same shape and found a fourth,
  `DetectInternalLeaks` (not named in the report but the identical bug).
  Fixed all four via a new shared `_merge_findings_respecting_suppression()`
  helper that calls `evaluate()` and appends the same
  `_build_suppression_overreach_change()` diagnostic `ApplySuppression`
  itself builds, replacing each detector's own hand-rolled dedup-and-filter
  loop. Added `TestLateDetectorSuppressionDiagnostic` (`test_diff_templates`'s
  CPO case, plus a new `DetectInternalLeaks` case) and updated the two
  existing late-detector tests (`test_experimental_removed_without_replacement_survives_broad_suppression`,
  `test_cpo_kind_changed_survives_broad_suppression`) to assert the
  diagnostic now appears — their prior comments explicitly called this out
  as a documented, not-yet-closed limitation; this round closes it.
  **Deliberately left open**: `checker.py`'s own `is_suppressed()` call
  sites (`_filter_suppressed_changes`, `_apply_surface_metrics`,
  `_filter_pattern_synthetic` — the last being the ADR-027
  `--pattern-verdicts` path D3 above already partially audited) have the
  same shape but a different call signature (`SuppressionList` +
  `suppressed: list[Change]` directly, not `PipelineContext`) and were not
  part of Codex's report; converting them needs its own signature-compatible
  helper and individual verification, not a blind find-and-replace — tracked
  as a follow-up, not fixed in this round.
- **`MarkReachability` itself never tagged a directly-public-header type's
  own change (Codex, fresh evidence).** `internal_leak.compute_leak_paths`
  only ever records *internal* types found while walking outward from the
  public surface — a type that IS the public surface (e.g. a header-only
  type never referenced by an exported function/variable, so nothing walks
  "into" it from elsewhere) never becomes a key in its result, so a raw
  change on that type's own layout got no tag at all, even though
  `RecordType.origin == ScopeOrigin.PUBLIC_HEADER` (ADR-024's opt-in
  `--public-header` scoping) is exactly the reliable signal already
  consulted for the late-detector findings in `diff_namespaces.py`/
  `diff_templates.py`. Fixed by building an origin-by-name map (reusing
  `diff_namespaces._origin_by_name`) alongside the existing leak-path walk
  and tagging `public_reachable=True`/`reachability_kind="direct_public_symbol"`
  directly for a change whose root type carries that origin, before falling
  back to the leak-path check. Without `--public-header` every origin is
  `ScopeOrigin.UNKNOWN`, so this degrades to the prior behavior
  automatically — purely additive, not a regression. Explicitly *not* the
  reverted "any non-internal-namespaced subject" heuristic this class's own
  docstring warns against: `ScopeOrigin.PUBLIC_HEADER` is an explicit opt-in
  tag, not a naming guess. Added `test_public_header_type_own_change_is_reachable`/
  `test_non_public_header_type_own_change_stays_untagged` to
  `test_reachability_aware_suppression.py`.
- **The public-header direct-tag above only looked at `RecordType` (Codex,
  fresh evidence).** `Function`/`Variable`/`EnumType` all carry the same
  `ScopeOrigin` field — a public-header function/variable/enum's own change
  had the identical gap the `RecordType` fix above closes. Extended the
  direct-tag lookup to all four declaration kinds via a small
  `_public_header_names()` helper, plus owner-stripping for
  `ENUM_MEMBER_REMOVED`/`ENUM_MEMBER_ADDED`/`ENUM_MEMBER_VALUE_CHANGED`/
  `ENUM_LAST_MEMBER_VALUE_CHANGED` — `diff_types.py` builds these findings'
  `symbol` as `"EnumName::member"`, and unlike `STRUCT_FIELD_*` kinds this
  isn't stripped by the shared `_root_type_name_for_change` (deliberately
  left that shared helper alone rather than changing its existing behavior
  for the unrelated leak-path check). Added
  `test_public_header_variable_own_change_is_reachable`/
  `test_public_header_enum_member_change_is_reachable` to
  `test_reachability_aware_suppression.py`.
- **`checker._filter_pattern_synthetic` had the exact `is_suppressed()` vs.
  `evaluate()` diagnostic gap the `post_processing.py` late-detector fix
  above closed (Codex, fresh evidence) — this is the ADR-027
  `--pattern-verdicts` path (D3 above), a separate module invoked from
  `checker._apply_pattern_verdicts_step` well after `MarkReachability`
  runs, so its `OPAQUE_INVARIANT_BROKEN`/`HANDLE_TYPE_CHANGED` synthetics
  never got the withheld-rule diagnostic either.** Unlike the four
  `post_processing.py` detectors, this function's signature doesn't take
  `PipelineContext` (a plain `SuppressionList` + `suppressed: list[Change]`
  instead) — the exact reason this call site was left as an open P1 roadmap
  item in the round above. Fixed anyway since the change was small and
  self-contained: `_filter_pattern_synthetic` now calls `evaluate()` and
  appends the same `_build_suppression_overreach_change()` diagnostic
  (imported from `post_processing.py`; no import cycle — `post_processing`
  does not import `checker`). Added
  `test_lost_opaqueness_withheld_broad_rule_gets_diagnostic` to
  `test_pattern_verdicts.py`. Narrows P1 roadmap item 6 to just the
  remaining two `checker.py` call sites (`_filter_suppressed_changes`,
  `_apply_surface_metrics`) — neither builds a fresh synthetic finding a
  suppression rule could plausibly want to match-but-withhold the same way
  (they filter pre-existing/aggregate findings, not late detector output),
  so closing them is lower priority than this one was.

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

### What the P0 slice did not fix (closed by the P1 slice below)

The oneDAL dispatcher case (`func_removed` on an internal template
specialization reached only via `DECL_CALLS_DECL` from a public inline
function — no layout evidence, so `internal_leak.py`'s
`_LEAK_TRIGGERING_KINDS`/BFS-over-`RecordType` walk structurally cannot see
it) was **not** closed by the P0 slice. `MarkReachability` reused only
`internal_leak.compute_leak_paths`, which walks type-layout reachability
(inheritance, by-value fields, signatures) — it had no access to the L5
semantic call graph (`source_graph.py`). The P1 slice below closes this gap.

## P1 slice: call-graph reachability, the overlay kind, and remaining plumbing

Implemented as a follow-up change on the same branch, closing P1 items 1, 2,
5, and 6 below in full and item 4 in full; item 3 (propagation-aware edge
semantics) is closed to the extent described under its own entry.

- **Item 1 (call-graph evidence).** New `internal_leak.compute_call_graph_leak_paths(snap, internal_namespaces)`
  walks the optional L5 source graph's `DECL_CALLS_DECL`/`DECL_REFERENCES_DECL`
  edges from every public entry (`buildsource.source_graph.is_public_dependency_node`),
  returning `internal_decl_name -> [formatted proof paths]` — the call-graph
  sibling to `compute_leak_paths`'s layout walk, reusing
  `source_graph_findings._dependency_reachability`/`_dependency_path`/
  `_format_dependency_path` (all three already existed, unwired for this
  purpose). `MarkReachability` now consults this as a second, independent
  evidence source: a change untouched by the layout walk (no field/base/
  signature evidence at all) can still be tagged `public_reachable=True` via
  a pure call/reference edge. Requires an embedded L5 graph
  (`--sources`/`--build-info`/`--header-graph`); returns `{}` and changes no
  behavior otherwise, mirroring `poi.resolve_changed_paths_public_impact`'s
  own degrade contract.
- **Item 2 (overlay `ChangeKind`).** New `internal_symbol_required_by_public_api`
  (`BREAKING_KINDS`, registered in `change_registry_suppression.py` alongside
  the P0 diagnostic to stay under `change_registry.py`'s line cap). Built by
  new `internal_leak.detect_call_graph_leaks`/`_build_call_graph_leak_change`,
  wired into the existing `DetectInternalLeaks` pipeline step alongside
  `detect_internal_leaks`. Triggers only on a change whose own kind is
  already `BREAKING_KINDS` (artifact-proven; **not** `API_BREAK_KINDS` — see
  the post-merge review round below) and whose subject is internal-namespaced
  and call-graph-reachable — per the
  authority rule (ADR-028 D3/ADR-041), the graph edge composes with and
  explains an already-proven break; it never manufactures one, exactly like
  `INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`'s own `BREAKING` classification.
- **Item 3 (edge semantics) — partially closed.** `reachability_kind` grew a
  third real value, `"symbol_availability"`, for the call-graph case —
  no longer the "two-value approximation" the P0 slice shipped with. The
  finer `DECL_CALLS_DECL` vs. `DECL_REFERENCES_DECL` distinction the item
  also names is preserved as text inside `reachability_proof_path` (via
  `_format_dependency_path`'s `--[EDGE_KIND]-->` annotation) rather than a
  further split of `reachability_kind` itself — a deliberate stopping point,
  not an oversight: a machine-readable call-vs-reference sub-enum is a
  reasonable further increment but wasn't required to close the item's core
  ask (distinguishing symbol-availability edges from the two layout-based
  kinds). Left as a candidate future refinement.
- **Item 4 (structured report fields).** `public_reachable`/
  `reachability_kind`/`reachability_proof_path` now appear as first-class
  fields (not just inside the `suppression_would_hide_public_break`
  diagnostic's prose) in JSON (`reporter._change_to_dict` **and**
  `_to_json_leaf`'s `_leaf_entry` — the latter handles root `TYPE_*` changes,
  the category the layout walk tags most often, and was easy to miss since
  it's a separate hand-rolled dict) and SARIF (`sarif._result_for`'s
  `properties`, camelCased per that format's convention). JUnit was left
  untouched — it doesn't surface `caused_by_type`/`correlated_change_kind`
  either, so adding reachability fields there would be new precedent, not
  parity.
- **Item 5 (configurable internal-namespace convention).** New
  `PolicyFile.internal_namespaces: list[str]` (parsed identically to
  `frozen_namespaces`), threaded via a new `PipelineContext.internal_namespaces`
  field through `PostProcessingPipeline.run()` (appended *after* the
  existing optional parameters, not inserted mid-signature — a Codex review
  on the PR caught that an earlier draft inserted it before
  `scope_to_public_surface`, which would have silently broken any positional
  caller of that parameter) to `MarkReachability`/`DetectInternalLeaks`/
  `DemoteUnreachableInternalChurn`. Deliberately **not** threaded into
  `DetectNamespacePatterns`'s `experimental_namespaces` — despite this
  item's own wording grouping all four steps together, that parameter
  governs an unrelated convention (the `experimental::` graduation
  namespace, a different default token set), and conflating the two would
  reintroduce a bug, not fix one.
- **Item 6 (remaining `checker.py` call sites).** `_filter_suppressed_changes`
  and `_apply_surface_metrics` now call `SuppressionList.evaluate()` and
  append the same `_build_suppression_overreach_change()` diagnostic
  `ApplySuppression`/`_filter_pattern_synthetic` already produce, instead of
  the boolean `is_suppressed()`.

**Post-merge review round (Codex), same P1 change:**

- **Mangled symbol vs. demangled label — item 1 was inert on real binaries
  (fresh evidence).** `compute_call_graph_leak_paths` keyed its result dict by
  `node.label` — the L5 graph's demangled qualified name for a
  `SOURCE_DECLARES`-backed decl (`ns::detail::train_ops_dispatcher`), or, for
  a call-graph-only fallback node, either the mangled name or a
  `#sha256:`-suffixed qualified name depending on provenance. But
  `diff_symbols.py` builds a real `FUNC_REMOVED` `Change` with
  `symbol=` the **mangled** linker name (`_ZN2ns6detail19train_ops_dispatcherEv`),
  and `_root_type_name_for_change` returns that verbatim for a
  function-shaped kind — so `detect_call_graph_leaks`'s lookup by `c.symbol`
  almost never matched `compute_call_graph_leak_paths`'s label-keyed result
  for a real, castxml/clang-parsed C++ removal; the whole item 1/2 mechanism
  only appeared to work in unit tests that hand-construct a `Change.symbol`
  equal to the graph label. Worse, `detect_call_graph_leaks` also
  pre-filtered its triggering-change candidates with
  `is_internal_type(root, ...)` — a check that splits on `"::"` — which a
  bare mangled name (no `::` at all) always fails, rejecting every real
  candidate before the (already-broken) lookup even ran.
  Fixed both: `compute_call_graph_leak_paths` now also resolves each
  internal target's own exported symbol via its `SOURCE_DECL_MAPS_TO_SYMBOL`
  edge (the same `binary_symbol://` identity
  `source_graph.localize_symbol()` already uses for the reverse direction)
  and records the proof paths under that mangled key too, alongside the
  existing label key — a node with no such edge (no linkage, e.g. fully
  inlined) gets no mangled key, but no `FUNC_REMOVED`-shaped `Change` could
  ever look one up anyway. `detect_call_graph_leaks` dropped its redundant
  `is_internal_type` pre-filter entirely: a hit in the call-path dict is
  already sufficient proof of "internal and call-graph-reachable", since
  `compute_call_graph_leak_paths` gates its own key insertion on
  `is_internal_type(node.label, ...)` (the qualified name, which does have
  `::` segments) before ever adding either key. Added
  `test_result_also_keyed_by_mangled_exported_symbol`/
  `test_func_removed_matches_via_mangled_symbol_not_label` to
  `test_internal_leak.py`, reproducing the real-world mangled-vs-label shape
  the prior tests' hand-picked matching names had masked.
- **Item 4's new fields needed a schema version bump (Codex).** The three
  new per-finding JSON fields (`public_reachable`/`reachability_kind`/
  `reachability_proof_path`) are additive optional keys per
  `abicheck/schemas/__init__.py`'s own documented policy ("additive changes
  — new optional keys… bump the MINOR component"), the same discipline every
  prior additive field (2.1 through 2.5) already followed with its own
  changelog comment — missed here even though the schema's
  `additionalProperties: true` meant no test caught it (unregistered keys
  validate anyway). Bumped `REPORT_SCHEMA_VERSION` to `"2.6"` with a matching
  changelog comment, added the three fields (with `reachability_kind`'s enum)
  to `compare_report.schema.json`, and re-synced the published
  `docs/schemas/v1/` copy via `scripts/publish_schemas.py`.
- **Header-graph mode still had the mangled-vs-label gap; `API_BREAK_KINDS`
  triggers were a category error (Codex, fresh evidence, two findings).**
  (1) The mangled-symbol-key fix above only helps when the L5 graph carries a
  `SOURCE_DECL_MAPS_TO_SYMBOL` edge — the build-integrated L4/L5 path
  (`source_graph.py`) creates one, but the header-only path (`header_graph.py`,
  `--header-graph`/the implicit dump path, no real build at all) never does,
  so the mismatch this review round already fixed once still applied for
  header-graph-only snapshots. Fixed by also trying each trigger's own
  `Change.qualified_name` (set by `EnrichSourceLocations` from `Function.name`
  — the same demangled name a graph node's `label` carries in *either* mode,
  independent of graph provenance) as a fallback lookup key in both
  `MarkReachability` and `detect_call_graph_leaks`, alongside the existing
  mangled-symbol key. (2) `detect_call_graph_leaks`'s trigger set was
  `BREAKING_KINDS | API_BREAK_KINDS`, but `API_BREAK_KINDS` is the
  `SOURCE_CONTRACT` evidence tier — "a source-level break that needs a
  recompile… not necessarily a shipped ABI break" per `checker_policy.py`'s
  own docstring — and most of its members (e.g. `inline_function_removed`,
  whose own inline comment reads "no exported symbol") have no removed
  linker symbol at all. Composing one into this overlay's "can fail to
  resolve this symbol at load time" description was a false binary-load-time
  claim for a change that was never one — the same category of mistake
  `_LEAK_TRIGGERING_KINDS`'s own hand-curated (not "every breaking-shaped
  kind") trigger set was designed to avoid. Restricted the trigger set to
  `BREAKING_KINDS` only. Extended `test_internal_leak.py` with
  `test_header_graph_mode_matches_via_qualified_name` (no
  `SOURCE_DECL_MAPS_TO_SYMBOL` edge, mangled `Change.symbol` +
  `qualified_name` set, matching only via the fallback key) and
  `test_api_break_kind_is_not_a_trigger` (an `API_BREAK_KINDS` member with
  call-graph evidence produces no overlay).

## Roadmap (not committed — scope/sequence per the usual planning process)

P1 is implemented (above); P2 remains open, numbering mirrors the original
review's priority tiers.

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
   `examples/case*/` additions now that P1 item 1's call-graph reachability
   is wired (previously blocked on it).

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

- `abicheck/post_processing.py` — `DEFAULT_PIPELINE`, `PipelineContext`,
  `MarkReachability`, `ApplySuppression`, `DetectInternalLeaks`,
  `DemoteUnreachableInternalChurn`
- `abicheck/internal_leak.py` — `compute_leak_paths`, `compute_call_graph_leak_paths`,
  `detect_internal_leaks`, `detect_call_graph_leaks`, `_LEAK_TRIGGERING_KINDS`,
  `_root_type_name_for_change`
- `abicheck/suppression.py` — `Suppression`, `SuppressionList`
- `abicheck/checker_types.py` — `Change`
- `abicheck/checker.py` — `_filter_suppressed_changes`, `_apply_surface_metrics`
- `abicheck/policy_file.py` — `PolicyFile.internal_namespaces`
- `abicheck/reporter.py`, `abicheck/sarif.py` — structured reachability fields
- `abicheck/buildsource/source_graph.py`/`source_graph_findings.py` — the L5
  graph and `_dependency_reachability`/`_dependency_path`/
  `_format_dependency_path` the P1 slice's call-graph walk reuses
- ADR-004 — Report filtering and deduplication (redundancy-before-verdict
  invariant this ADR deliberately does not disturb)
- ADR-013 — Suppression system design (pipeline-ordering rationale this ADR
  amends)
- ADR-024 — Public ABI surface resolution (audit-ledger / never-silently-drop
  convention this ADR follows for `suppression_would_hide_public_break`)
- ADR-028 — Build-source evidence pack (the authority rule the P1 overlay
  kind's `BREAKING` classification relies on: L3-L5 evidence may explain/
  correlate an artifact-proven break, never manufacture one)
- ADR-041 — Compiler-facts semantic impact graph (`PUBLIC_API_INTERNAL_DEPENDENCY_ADDED`,
  the L5 graph schema the P1 slice's call-graph walk reuses)
