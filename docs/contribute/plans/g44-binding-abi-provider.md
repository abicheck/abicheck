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

### Comparison semantics

This is fundamentally a **new kind of cross-module compatibility check**,
not a per-library `ChangeKind` — closer in shape to the bundle layer's
sibling-DSO analysis (G38) than to a single-binary diff. The natural home
is a new `compare/`-owned matcher, invoked from `workflows/`-level
bundle/multi-artifact scan coordination (existing recognition in
`abicheck/scan_engine.py` is the entry point to extend from, not the
implementation's home — see "Files & surfaces" above) that:

1. Extracts each module's binding surface.
2. Finds declared shared native types between module pairs (via whatever
   registration-scope evidence is available — global-scope types are
   candidates for every other global-scope module in the same process;
   module-local types are never candidates).
3. Flags an incompatibility when two modules sharing a candidate type
   disagree on binding ABI/internals version, C++ stdlib ABI, debug/release
   identity, or free-threaded mode.

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

## Out of scope

- Modeling every C++ binding framework in general (Boost.Python, SWIG,
  cppyy) — this plan is scoped to pybind11/nanobind specifically, per the
  review's own framing and this codebase's existing Python-extension focus.
- Runtime instrumentation (actually importing and exercising both modules
  together in a live interpreter) — this is a static, binary-evidence-first
  check, consistent with the rest of this tool's design.
