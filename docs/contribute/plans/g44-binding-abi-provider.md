---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G44 — pybind11/nanobind binding-ABI provider

## Problem

ABICheck already recognizes CPython extension modules regardless of
builder (`abicheck/python_ext.py`'s module docstring: "whether produced by
Cython, pybind11, nanobind, or a hand-written C extension... the
recognition is uniform across builders"), audits CPython/`abi3` usage
(`abicheck/stable_abi.py`, G14), compares adjacent `.pyi` files
(`abicheck/python_api.py`, G23), and analyzes native libraries inside
wheels. None of that answers a materially different question: **when two
extension modules built with pybind11 or nanobind exchange a registered
C++ type across a module boundary, do their binding runtimes actually
agree on how that type is represented?**

pybind11/nanobind maintain their own internal type-registration state
(a per-process or per-module registry keyed by binding-library "internals"
version, C++ RTTI identity, and a set of ABI-affecting compile-time
choices). Two modules can each be individually valid, importable, and
functionally correct in isolation, and still be incompatible with each
other the moment they try to share one C++ object across the boundary —
this is invisible to every mechanism above, none of which model the
binding runtime's own identity.

## Goal & acceptance criteria

Add a normalized **binding surface** extraction and comparison, collecting:

```
framework: pybind11 | nanobind
framework semantic version
binding ABI / internals version
platform/compiler ABI identity
C++ standard library ABI
debug/release identity
regular / free-threaded CPython mode
stable-ABI mode
global / module-local / domain-scoped registration
declared shared native types
```

Use binary evidence first (symbols/sections a real build of either
framework embeds — see Design below) and an optional build-emitted
manifest second, with a manifest-vs-binary contradiction reported
explicitly rather than silently preferring one source.

### Acceptance test

A wheel contains `_core.so` and `_geometry.so`. Both import successfully in
isolation. They exchange one registered C++ type between them. Their
pybind11 internals identities differ (e.g. built against different
pybind11 minor versions, or with a different `PYBIND11_INTERNALS_VERSION`-
affecting compile flag). ABICheck must report a real cross-module
incompatibility. A negative control using `py::module_local` types (which
pybind11 documents as deliberately *not* shared across the global registry)
must remain non-breaking.

**A second acceptance test for the cross-release axis, added per the
`compare()` design correction above**: the same `_core.so`/`_geometry.so`
internals mismatch from the first test exists **identically** in both an
old and a new release (nothing changed about it between releases).
Comparing the old release against the new release must **not** re-report
it as a fresh incompatibility — the per-release module-pair graph shows
the same disagreement on both sides, so `compare(old, new, policy)` finds
no relationship-level delta. A third variant: the mismatch is present in
the old release and genuinely fixed in the new one (both modules rebuilt
against the same pybind11 internals version) — this *must* report a
relationship-level change (the pair moved from disagreeing to agreeing),
distinguishing "the break was fixed" from "nothing changed." **A fourth
variant, added per the pair-creation/deletion correction below**: the old
release contains only `_core.so`; the new release adds `_geometry.so`,
which shares a global native type with `_core.so` under incompatible
internals. This pair did not exist in the old release's own module-pair
graph at all — `compare()` must still report a real cross-module
incompatibility for it (absent → disagreeing), not silently pass because
neither side of the pair existed on both releases. **A fifth variant,
added per the module-identity-matching correction below**: `_geometry.so`
from the old release is renamed to `_geometry_v2.so` in the new release,
with the identical incompatible internals mismatch against `_core.so`
preserved unchanged. `compare()` must recognize this as the *same* pair
via stable module identity (not filename) and report **no** relationship-
level change — the naive filename-keyed reading (old pair vanishes, a
new one appears) must not fire as a fresh incompatibility. **A sixth
variant, added per the per-type keying correction below**: `_core.so`
and `_geometry.so` already disagree on shared type A in the old release
(and continue to, unchanged, in the new release); the new release
additionally introduces a fresh internals mismatch on a *different*
shared type B between the same two modules, previously compatible.
`compare()` must report a real cross-module incompatibility for type B
specifically — a module-pair-only aggregate that already reads
"disagreeing" from type A must not mask type B's own, independent
regression.

## Design

### Where the identity actually lives

Both frameworks expose a discoverable identity, though by different
mechanisms — this needs verifying against real compiled `.so` files for
both frameworks (pybind11 and nanobind each have their own internals-tag
scheme, and nanobind's has changed across its own major versions) before
committing to one extraction strategy:

- **pybind11**: the internals struct is looked up via a Python capsule
  named with an ABI-tag string embedding `PYBIND11_INTERNALS_ID`-derived
  components (compiler, standard library, build type, Python version) —
  this string is discoverable from the binary's own embedded constants
  (typically as a string literal or via the capsule name registered at
  module init) without needing to execute the module. Confirm the exact
  extraction mechanism (symbol scan vs. section scan vs. requiring a
  controlled import) empirically against a real build before finalizing.
- **nanobind**: similar in spirit — an ABI tag baked in at compile time,
  distinct from pybind11's, and versioned independently across nanobind's
  own releases.

Registration scope (`global` / `module_local` / a domain-scoped variant)
determines whether a given C++ type registration is even a candidate for
cross-module sharing at all — a `module_local` type is pybind11's own
documented mechanism for *avoiding* this exact class of incompatibility, so
it must be a true negative in this detector, not merely unflagged by
omission.

### Binary evidence first, manifest second

Prefer deriving the binding surface from the compiled `.so` directly (no
external build metadata required, matching this codebase's general
preference for binary-evidence-first extraction — see `python_ext.py`'s
existing recognition approach). Support an optional, additive
build-emitted manifest (a project can declare its own pybind11/nanobind
version and compile flags explicitly, the same shape `build-output.json`
already uses for other build facts) for cases the binary alone can't
disambiguate (e.g. two internals versions that happen to serialize
identically in the fields the binary evidence can see). When both are
present and disagree, report the contradiction explicitly rather than
silently trusting one — this is the same principle G39's producer-receipt
work applies to compiler-identity evidence, applied here to binding-runtime
identity.

### Comparison semantics — implement the SciPy roadmap's `SurfaceProvider` interface, not a bespoke one

This is fundamentally a **new kind of cross-module compatibility check**,
not a per-library `ChangeKind` — closer in shape to the bundle layer's
sibling-DSO analysis (G38) than to a single-binary diff. **This plan's
provider must not invent its own shape**: two existing documents already
specify a canonical, shared plugin interface for exactly this class of
evidence provider, and `BindingAbiProvider` is explicitly named as one of
its intended implementations —
[`docs/contribute/scipy-scientific-python-roadmap.md`](../scipy-scientific-python-roadmap.md)'s
"Recommended architecture" section defines:

```
SurfaceProvider
  identify(artifact)
  collect(artifact, context) -> SurfaceFacts
  compare(old, new, policy) -> Changes
  coverage() -> CoverageRecord
```

and
[`docs/contribute/python-build-ecosystem-positioning.md`](../python-build-ecosystem-positioning.md)
(the "Proposed provider shape" section) says explicitly: "`BindingAbiProvider`
should be one more implementation of the `SurfaceProvider` interface... not a
separately-invented interface — the two docs should not end up specifying
two incompatible plugin shapes for the same evidence-provider concept."
Adopt that contract here rather than the ad hoc matcher an earlier draft of
this plan sketched:

- **`identify(artifact)`** — **needs a real, new pybind11/nanobind-specific
  discriminator; `python_ext.py`'s existing recognition cannot be reused
  directly for this, confirmed by reading it, not assumed.**
  `detect_python_extension()`'s own module docstring states its
  recognition is *deliberately* uniform across builders: "Cython/pybind11/
  nanobind/C all land here because they all emit the same `PyInit_*`
  export and link `libpython`" — it answers "is this a CPython extension
  module at all," not "which framework built it." Using it directly as
  `BindingAbiProvider.identify()` would misclassify every Cython or
  hand-written C extension as a pybind11/nanobind candidate, producing
  false incomplete-coverage findings (this provider reporting on a module
  it has no business analyzing) or false binding-ABI findings (comparing
  two unrelated Cython modules' unrelated internals as if they were a
  pybind11 type-registration pair). `python_ext.detect_python_extension()`
  is still useful here, but only as the **broad prefilter** — "is this a
  CPython extension at all" — that this provider's own, new, narrower
  discriminator runs after, not instead of. That new discriminator needs
  real, verified framework-specific evidence (no such recognition exists
  anywhere in this codebase today, confirmed by grep — every other
  `pybind11`/`nanobind` mention is prose, not a binary signal check):
  framework-specific mangled-symbol namespaces (`pybind11::`/`nb::`
  appearing in the module's own exported/imported symbol names), an
  RTTI `type_info` string identifiable as one of those frameworks'
  internal registration types, or an explicit build-emitted manifest
  field (this plan's own "optional build-emitted manifest" fallback,
  already planned for `collect()`, extended to also carry a declared
  framework identity) — verified against real compiled pybind11/nanobind
  extensions before being trusted, not assumed from documentation alone.
- **`collect(artifact, context) -> SurfaceFacts`** — the normalized binding
  surface described in "Goal & acceptance criteria" above (framework,
  version, internals identity, registration scope, declared shared native
  types), from binary evidence first, an optional build-emitted manifest
  second.
- **`compare(old, new, policy) -> Changes`** — **must stay a genuine
  old-release-vs-new-release comparison of one module's own surface facts,
  not a repurposed sibling-module comparison within one release — a real
  design confusion caught by a fresh review round, not a hypothetical
  one.** An earlier draft of this bullet defined `compare()` as "find
  declared shared native types between two modules... flag an
  incompatibility when two modules sharing a candidate type disagree,"
  treating its `old`/`new` parameters as two sibling modules in one
  release. That discards the temporal dimension the shared
  `SurfaceProvider` contract exists to carry, and produces wrong results
  either way: a pre-existing internals mismatch between two sibling
  modules that is genuinely unchanged from one release to the next would
  be re-emitted as a "new" incompatibility on *every* release comparison
  (nothing tracks that it already existed last time), while a real fix —
  resolving an old mismatch between two siblings — has no release-level
  delta to report under this shape at all, since "old"/"new" were never
  the release axis in the first place.

  The cross-module (sibling) relationship question and the cross-release
  (did-it-change) question are genuinely two different axes, and must
  stay two stages, not one repurposed function:
  1. **Per-release module-pair compatibility graph, keyed by (module pair,
     shared native type) — not by module pair alone.** (a new,
     `compare/`-owned computation, run once per release): for one
     release's full set of collected modules, find every candidate shared
     native type across sibling pairs (the registration-scope logic
     above) and record, per **(pair, type)** combination, whether the pair
     agrees on binding ABI/internals version/C++ stdlib ABI/debug-release
     identity/free-threaded mode for *that specific type* — the same
     shape G38's bundle-internal detectors already establish for a
     *release's own* cross-DSO relationships, applied here to Python
     binding modules instead of DSOs.

     **A single aggregated agree/disagree boolean per module pair — an
     earlier draft of this stage's own shape — silently loses regressions
     once a pair shares more than one candidate type, confirmed by a
     fresh review round.** If `_core.so`/`_geometry.so` already disagree
     on shared type A, and a new release introduces a *fresh*
     incompatibility on a *different*, previously-compatible shared type B
     between the same two modules, a pair-level aggregate (however it
     combines multiple types' states — worst-of, any-disagree, or
     otherwise) reads "disagreeing" on both the old release and the new
     one: nothing about the *pair's own aggregated state* changed, so
     stage 2's diff — which compares pair-level states between releases —
     emits nothing, even though the release genuinely introduced a new,
     real incompatibility on type B. Keying and diffing by (pair, type)
     instead of by pair alone closes this: type A's own (pair, type)
     entry stays `disagreeing → disagreeing` (correctly silent, nothing
     changed for that type), while type B's own entry transitions
     `agreeing → disagreeing` (correctly emits a `Change`, per the
     already-established transition table) — the two types' fates never
     collapse into one shared pair-level signal.
  2. **`compare(old, new, policy) -> Changes`** then compares *that graph*
     between the old release and the new release — i.e. `old`/`new` are
     genuinely the old-release and new-release module-pair graphs (or,
     per-module, the module's own `SurfaceFacts` across releases,
     whichever the SciPy roadmap's own `SurfaceProvider` contract
     specifies more precisely once read against this shape) — emitting a
     `Change` only for a pair whose *relationship* changed, never for a
     relationship merely re-observed as unchanged.

     **The diff must cover pair creation/deletion explicitly, not only
     the two flip transitions on an already-existing pair — a real gap
     confirmed by a fresh review round.** "A previously agreeing pair now
     disagrees, or vice versa" only names the two transitions where the
     pair exists on *both* sides. It silently misses the case a new
     release actually introduces a break through: a new module
     (`_geometry.so`) is added in the new release and shares a global
     native type with an existing module (`_core.so`) under incompatible
     internals — the new graph contains a disagreeing pair with *no old
     counterpart at all*, so "previously agreeing... now disagrees"
     doesn't apply (there is no "previously" state for a pair that didn't
     exist), and the stated rule emits nothing even though the release
     genuinely introduced a real cross-module incompatibility. The full
     transition table this stage must handle:
     - **absent → disagreeing**: a pair that didn't exist in the old
       release (one or both modules new, or the shared type wasn't
       candidate-eligible yet) now disagrees in the new release — **emit
       a `Change`**; this is exactly as real a break as an
       agreeing-to-disagreeing flip, just introduced by addition rather
       than regression.
     - **disagreeing → absent**: a disagreeing pair from the old release
       no longer exists in the new one (a module removed, or the shared
       type no longer candidate-eligible) — the incompatibility is moot
       (nothing to break at runtime any more); record it as an
       informational/resolved-by-removal note if this plan's reporting
       shape has room for one, but it is not a regression to gate on.
     - **agreeing → disagreeing** / **disagreeing → agreeing**: the two
       transitions already named — a real regression and a real fix,
       respectively.
     - **absent → agreeing** / **agreeing → absent**: no finding either
       way — nothing incompatible existed or exists.

     **This transition table is only correct once module-pair identity is
     matched across releases by something more durable than a bare module
     filename — a second real gap in the same area, confirmed by a fresh
     review round.** The table above decides "absent" vs. "present" by
     graph-key presence, but says nothing about how a pair's *key* is
     derived or matched between the old release's graph and the new
     release's graph. If the key is (or reduces to) the module's own
     filename, a module that is merely **renamed** between releases while
     keeping the identical relationship — `_geometry.so` renamed to
     `_geometry_v2.so` in the new release, still sharing the same
     incompatible internals with `_core.so` as before — reads as two
     independent events under naive filename-keyed matching: the old pair
     `(_core.so, _geometry.so)` disappears (disagreeing → absent) and a
     new pair `(_core.so, _geometry_v2.so)` appears (absent →
     disagreeing) — the latter is then wrongly gated as a newly
     introduced break by the rule above, when nothing about the actual
     relationship changed at all, only the file's name. Closing this
     needs module identity matched across releases by a **stable,
     content-based identity** before the transition table is ever applied
     — analogous to (though for a different artifact kind than) this
     codebase's own existing `binary_fingerprint.py` rename-detection
     precedent for whole-library renames (`RenameCandidate`/
     `match_renamed_functions`'s exact/size/fuzzy matching cascade) — not
     a second, independent invention of rename detection for Python
     binding modules.

     **The candidate identity this section first proposed — the module's
     own resolved internals/registration identity — is wrong, and a
     second review round caught it: it is the compatibility axis being
     *compared*, not an independent module identity, and using it as the
     matching key breaks in both directions the rename-matching problem
     was trying to fix.** (1) It is not module-*unique*: two genuinely
     distinct sibling modules built with the same toolchain/pybind11
     version legitimately share the identical internals tag — matching on
     it risks collapsing two different modules into one graph node. (2) It
     is not *stable* across exactly the event this detector exists to
     catch: a module whose internals tag genuinely changes between
     releases (a real ABI break) would, under this key, fail to match its
     own prior-release self at all — reading as pair deletion plus an
     unrelated pair creation, which the already-fixed
     absent-→-disagreeing rule then wrongly gates as a "new" break, for a
     module that was simply upgraded in place, not added. A module that is
     *both* renamed *and* has a genuinely changed internals tag in the
     same release is the sharpest failure case: neither the filename nor
     the internals identity survives the transition, so nothing proposed
     so far can match it to its old self.

     This needs a genuinely independent identity signal, not reached for
     in this pass — candidates worth evaluating, none yet validated
     against real fixtures: the module's own Python import name/path
     (survives a `.so` filename rename, though not a Python-level module
     rename — a narrower but real class of stability); the set of
     natively-registered type/class names the module exports (independent
     of the internals *version* tag, though it can still collide across
     modules that happen to register identically-named types); or an
     ambiguity-safe fingerprint cascade in the literal shape
     `binary_fingerprint.py` already establishes (exact match, then a
     narrower heuristic, then explicit "no confident match" rather than
     guessing) rather than a single field. This is genuinely new matching
     logic this phase must design and test against real fixtures — a
     renamed-module fixture (and the compounding renamed-*and*-changed
     case above) is a required acceptance case, not an edge case to
     defer, and this plan does not yet have a validated answer for it.
  This may be implemented as `compare()` itself internally invoking stage
  1 for both releases before diffing the two graphs, or as a separate
  bundle-level reconciliation stage this plan's own coordination
  (`workflows/`) invokes alongside `compare()` — either satisfies the
  contract; repurposing `old`/`new` as two sibling modules within one
  release does not.
- **`coverage() -> CoverageRecord`** — this provider's own evidence-
  completeness signal (did extraction fully resolve both modules' internals
  identity, or degrade), following the same "explicit incomplete-coverage
  result, never silent" discipline this plan's other sections already
  establish (see the C++-stdlib-ABI/registration-scope section above).

**Before designing this provider in detail, evaluate `SurfaceProvider`
adoption itself against [ADR-032](../adr/032-evidence-extractor-plugin-interface.md)
(the existing extractor-plugin interface) and
[ADR-034](../adr/034-managed-runtime-and-non-c-abi-frontends.md) (non-C-ABI
frontend scope)** — the positioning doc's own words: "Adopting
`SurfaceProvider` is a materially larger step than the current
evidence-tier model," and this evaluation is a real prerequisite, not a
formality, since `SurfaceProvider` may end up subsuming or reshaping how
ADR-032's plugin interface is expressed. If that evaluation concludes
`SurfaceProvider` itself is not yet ready to adopt, implement
`BindingAbiProvider` against the interface it specifies anyway (the shape
above), so the eventual `SurfaceProvider` migration is a registration
change, not a rewrite — never invent a third, `BindingAbiProvider`-only
shape in the meantime. This is also the deciding structural constraint for
package placement: `identify()`/`collect()` are `extract/`-owned, the
normalized `SurfaceFacts` type is `model/`-owned, `compare()` is
`compare/`-owned, and whatever invokes this provider from bundle/
multi-artifact scanning (existing recognition in `abicheck/scan_engine.py`
is the entry point to extend from, not the implementation's home) is
`workflows/`-owned — see "Files & surfaces" below.

## Files & surfaces

This is new code, so it must route to ADR-061's target responsibility
owners (root `AGENTS.md`'s "Task routing and dependency direction" table)
rather than growing the flat, legacy root-module families
(`abicheck/python_ext.py`, `abicheck/scan_engine.py`) that predate that
migration — new code imports the canonical implementation modules, never
extends a legacy facade, and `scripts/check_architecture.py` gates exactly
this:

- **`extract/`** — the binding-surface extractor itself (a new
  `extract/binding_abi/` submodule, or a sibling module inside whatever
  `extract/` package already owns Python-extension binary reading) — this
  is "read a binary/debug fact," ADR-061's `extract/` row. Keep pybind11
  and nanobind as two clearly-separated extractors behind one shared
  surface type, since their tag schemes are independently versioned and
  will drift independently.
- **`model/`** — the normalized binding-surface value type (framework,
  version, internals identity, registration scope, declared shared native
  types) shared between extraction and comparison — ADR-061's `model/` row
  ("add an ABI entity/value shared across stages").
- **`compare/`** — the raw old/new binding-surface matching and
  incompatibility identification (which module pairs share a candidate
  type, whether their surfaces disagree) — ADR-061's `compare/` row
  ("match old/new entities or identify a raw change").
- **`workflows/`** — bundle/multi-artifact scan coordination that invokes
  the new compare step alongside G38's existing bundle detectors, rather
  than adding a second, independent extraction/comparison invocation
  inside legacy `scan_engine.py` directly.
- **`policy/`**/`report/` — new `ChangeKind`s and their gating/reporting,
  following the standard four-step procedure in the root `AGENTS.md`
  ("Adding a new ChangeKind") — e.g. something in the shape of
  `binding_abi_mismatch`, `binding_internals_version_mismatch`. Registered
  in `abicheck/change_registry.py` (or a topic-specific sibling
  `change_registry_<topic>.py`) per that procedure, not hand-added to
  `BREAKING_KINDS`/etc.
- `abicheck/python_ext.py`/`abicheck/scan_engine.py` gain only the minimal
  recognition hook needed to invoke the new `extract/`/`workflows/` code
  from an existing entry point — implementation logic belongs in the new
  packages above, not grown inline in either legacy module.
- `docs/reference/change-kinds.md` / `detector-spec` — documentation for the
  new kinds, per the existing doc-generation pipeline.

Before writing code, check `abicheck/architecture/` (the executable
ADR-061 contract) and its no-growth inventory for the exact current package
boundaries and import-direction rules — this section names the target
owners, not the precise module paths, since the migration is incremental
and the exact package layout may have moved by the time this plan is
picked up.

## Tests

- Unit tests on the extractor against real compiled pybind11 and nanobind
  modules (built as `integration`-marked fixtures, since this needs a real
  toolchain and the real libraries installed — mirror how existing
  `python_ext.py`/`stable_abi.py` tests build real extension fixtures).
- The acceptance test above as an `integration` end-to-end case: two
  modules, mismatched internals, shared type, real incompatibility
  reported; a `module_local` negative control.
- A version-skew matrix test: same framework, adjacent versions known to
  change the internals tag, confirming the mismatch is detected; same
  framework, versions known *not* to change the tag, confirming no false
  positive.

## Effort & risk

XL. The largest risk is empirical, not architectural: neither framework
publishes a single canonical, stable location for its internals identity
across all its own historical versions, so the extraction logic will need
real fixtures spanning multiple pybind11 and nanobind releases to validate
against, and some of that surface may need to degrade to "unknown, treat
conservatively" for older/unusual builds rather than guessing. Scope the
first slice to whatever pybind11/nanobind versions are readily buildable
in this repo's own test/CI toolchain, and treat broader version coverage
as a follow-on rather than a blocker to shipping the first real detector.
The `identify()` framework discriminator (see "Comparison semantics" above)
carries the identical empirical risk one step earlier: no existing
recognition to build on, so its mangled-symbol-namespace/RTTI signal
choice needs the same real-fixture validation before being trusted as a
gate for whether this provider runs on a module at all — a false negative
here means the whole provider silently never fires; a false positive means
it misclassifies an unrelated Cython/hand-written extension. The
two-stage `compare()` design (per-release module-pair graph, then a
cross-release diff of that graph — see "Comparison semantics" above)
adds real architectural surface beyond a single matching function, but is
still additive to this estimate rather than a new risk category: it
reuses G38's own established shape for a per-release relationship graph,
applied to a different artifact kind.

## Out of scope

- Modeling every C++ binding framework in general (Boost.Python, SWIG,
  cppyy) — this plan is scoped to pybind11/nanobind specifically, per the
  review's own framing and this codebase's existing Python-extension focus.
- Runtime instrumentation (actually importing and exercising both modules
  together in a live interpreter) — this is a static, binary-evidence-first
  check, consistent with the rest of this tool's design.
