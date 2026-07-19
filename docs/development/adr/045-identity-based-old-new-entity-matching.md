# ADR-045: Identity-Based Old/New Entity Matching

**Date:** 2026-07-19
**Status:** Accepted — implemented for `RecordType` and `EnumType`.
**Decision maker:** Nikolay Petrov (@napetrov)

---

## Context

A false-positive/false-negative acceptance spike against the real `pvxs`
C++ library (`validation/pvxs-abicheck-acceptance-2026-07-18.md`) traced one
finding back to a structural gap: `diff_types.py`'s old/new `RecordType`
matching keyed its comparison maps by the bare declaration name
(`{t.name: t for t in old.types}`). Two distinct classes sharing a bare leaf
name in different namespaces (or two unrelated `std::*::_Impl` template
internals pulled in transitively) could collide in that dict — whichever one
a `for t in types` loop happened to insert last silently won the bare-name
slot, and the other was compared against the wrong counterpart on the
opposite old/new side. The result depended on **snapshot list insertion
order**, which is not a property that should ever influence a diff.

The immediate fix (PR #608, first round) added `RecordType.qualified_name`
(populated by both header-mode dumpers, castxml and clang) and a
namespace-qualified matching map (`diff_helpers.TypeMap`) for the `RecordType`
detectors in `diff_types.py`. Three follow-up review rounds then found the
same collision pattern reopened in progressively subtler ways: a schema-
evolution bare-name fallback needed to work in both matching directions, and
that fallback itself needed to refuse to fire when the *probing* side's own
bare name was ambiguous — otherwise the compatibility shim reintroduced the
exact collision it existed to paper over.

Once the mechanism was solid for `RecordType`, an explicit broader-scale
review of the codebase found the identical bug, live, in two more places:

- `diff_symbols.py` had never adopted `TypeMap` — its own four call sites
  (`_diff_functions`'s virtual-method-owner resolution,
  `_diff_ctor_overload_ambiguity`, `_diff_access_levels`,
  `_diff_anon_fields`) still built plain bare-name dicts, independently
  reintroducing the same false-positive/false-negative class this ADR's
  predecessor work had just fixed for `diff_types.py`.
- `EnumType` had no `qualified_name` equivalent at all, so
  `diff_types.py`'s own `_diff_enums`/`_diff_enum_renames`/
  `_diff_enum_deprecated` were exposed to the identical bare-leaf-name
  collision `RecordType` had just been fixed for.

Each of these was closed by hand, one file at a time, by someone who
happened to go looking. That is the actual problem this ADR records a
decision about: **the fix, as delivered, was a per-file patch repeated by
audit — not a principle any future detector author would discover on their
own.** A new detector that matches old/new entities by writing its own
`{e.name: e for e in ...}` dict is unremarkable-looking code; nothing about
it looks wrong until a real binary with a same-leaf-name collision exercises
it.

## Decision

**Old/new entity matching must use the most specific available identity,
with an ambiguity-safe fallback on both sides.** Concretely:

1. Prefer a namespace/scope-qualified identity over a bare declaration name
   whenever one is available. A bare name is a legitimate matching key only
   when nothing more specific exists for that entity kind (DWARF-only
   snapshots, which have no separate bare/qualified split at all — the
   qualified spelling already lives directly in `.name`).
2. When falling back to a less specific identity for schema-evolution
   compatibility (an older snapshot format, a producer that never populated
   the specific field), the fallback must be **safe on both sides of the
   comparison**: it may only resolve when the *less specific* key is itself
   unambiguous on the side doing the probing. An ambiguous fallback key must
   resolve to "no match", never to an arbitrarily-chosen candidate.
3. This applies uniformly to every entity kind a detector matches old vs.
   new by name — `RecordType`, `EnumType`, and any future kind with the same
   bare/qualified shape — not as a special case hand-implemented per
   detector.

### Implementation

The principle is realized as one generic mechanism in `diff_helpers.py`,
not duplicated per entity kind:

- `type_map_key(t)` — `t.qualified_name or t.name`: the qualified identity
  when known, the bare name otherwise.
- `TypeMap` — a `Mapping[str, Q]` (generic over any `_QualifiedNamed`
  structural type — `RecordType` and `EnumType` both satisfy it) keyed
  canonically by `type_map_key`, carrying a **collision-safe bare-name
  alias** used only by `get`/`in`, never by `items`/`values`/iteration (so a
  detector loop still processes each entity exactly once). The alias for a
  given bare name is only registered when that bare name is unambiguous
  within the map being built — i.e. not already claimed by a second,
  distinct qualified identity in the *same* snapshot.
- `TypeMap.bare_name_is_unambiguous(bare)` — exposes that same check so a
  cross-map lookup can decide, from the *probing* side, whether retrying
  with the bare name is safe.
- `lookup_matched_type(own, other, t)` — the one bidirectional, ambiguity-
  safe lookup every detector should call instead of hand-rolling one: try
  `other[type_map_key(t)]` first; only if that misses, and only if `t`'s own
  bare name is unambiguous in `own`, retry `other[t.name]`.

`RecordType.qualified_name` and `EnumType.qualified_name` are both populated
the same way on both header-mode dumpers (castxml's `context`-chain walk,
clang's AST `scope` tuple), and both left `None` when the entity is at
global scope or the dumper couldn't determine it — `.name` itself stays
bare on every producer (including DWARF's synthetic split) specifically so
`TypeMap`'s canonical key computation and any direct `.name` comparison
elsewhere in the codebase keep matching consistently.

Consumers, as of this ADR: `diff_types.py`'s `RecordType` detectors (vtable/
layout/field/access/anonymous-field/base-class families) and its three enum
detectors (`_diff_enums`, `_diff_enum_renames`, `_diff_enum_deprecated`);
`diff_symbols.py`'s four call sites listed above.

## Consequences

- **New detectors get this for free** by using `build_type_map`/
  `lookup_matched_type` instead of a bare dict comprehension — the
  ambiguity-safety is centralized, not something each detector author has
  to re-derive.
- **Regression coverage is generalized, not per-detector.** A Hypothesis
  property (`tests/test_detector_properties.py`,
  `test_same_leaf_name_matching_is_order_independent` /
  `test_same_leaf_name_enum_matching_is_order_independent`) generates two
  distinct qualified entities sharing a bare leaf name, randomizes snapshot
  list insertion order, and asserts the emitted diff is identical regardless
  of order — it does not know or care which detector fires, so it catches
  any future detector (for `RecordType`, `EnumType`, or a later entity kind
  reusing the same `TypeMap` machinery) that reintroduces a bare-name-keyed
  matching map, without anyone having to audit that detector by hand.
  Deterministic unit tests (`tests/test_diff_symbols_type_matching.py`,
  `tests/test_diff_enum_type_matching.py`) additionally pin the exact
  before/after behavior for each of the seven call sites this ADR's work
  touched, each confirmed (via `git stash` against the pre-fix code) to
  actually fail before the fix and pass after.
- **A known, deliberately unresolved gap remains:** when `qualified_name` is
  `None` on *both* sides for two entities that are NOT actually the same
  (one at true global scope, one whose dumper simply couldn't determine its
  namespace), `type_map_key` falls back to the bare name for both and they
  are indistinguishable to this mechanism. This is the same ambiguity
  `RecordType.qualified_name`'s own docstring already documents as
  unresolvable with the current data model (it would need the dumper to
  distinguish "confirmed global scope" from "unknown scope", which no
  current backend does) — this ADR does not attempt to close it, only to
  make sure it is the *one* remaining gap rather than one of several
  independently-discovered ones.

## Alternatives considered

- **Per-detector fixes, no shared abstraction.** This is what happened
  organically before this ADR (each of the seven call sites was patched by
  hand once someone noticed it) and is explicitly what this decision is
  reacting against: it scales with audit effort, not with test coverage.
- **A separate `EnumMap` duplicating `TypeMap`'s logic.** Rejected in favor
  of generalizing `TypeMap` over a `Protocol` (`_QualifiedNamed`, requiring
  `name`/`qualified_name` attributes) — the matching/ambiguity logic is
  identical for any entity kind with this bare/qualified shape, and a
  second copy would only reintroduce the "fixed once, forgotten as a
  pattern" failure mode this ADR is about.
- **Always require the qualified identity, no bare-name fallback.** Rejected
  because it would break matching against snapshots serialized before
  `qualified_name` existed, or produced by a backend that never populates
  it (e.g. DWARF-only mode structurally has no separate qualified field) —
  the fallback is necessary for schema-evolution compatibility; the fix is
  making that fallback ambiguity-safe, not removing it.
