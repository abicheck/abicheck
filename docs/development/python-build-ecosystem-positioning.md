# Where abicheck Fits in the Python Build Ecosystem

**Status:** Proposed — vision/roadmap doc, same genre as the
[SciPy / Scientific-Python Roadmap](scipy-scientific-python-roadmap.md) but
focused on the packaging/build-tooling ecosystem (scikit-build-core,
cibuildwheel, rattler-build, pybind11, nanobind) rather than the scientific
stack itself. Nothing here is implemented; it is recorded per the pattern in
[`backlog.md` § "Other deferred roadmap items"](backlog.md#other-deferred-roadmap-items).
No registry entries exist yet — turning any section below into real work
means adding a `UC-*` entry to
[`usecase-registry.yaml`](usecase-registry.yaml) and a plan file first, per
[`plans/index.md`](plans/index.md).

**Origin:** external roadmap review (feedback captured and reorganized
below), 2026-07.

---

## The positioning statement

> **abicheck should be the release-compatibility layer for compiled Python
> distributions — not another build backend, wheel repairer, or package
> builder.**

A native Python release pipeline runs through several tools that each answer
a narrow question. abicheck should answer the one none of them do: *did this
release break existing consumers, or narrow the set of environments it
claims to support?*

| Layer | Main question | What abicheck adds |
|---|---|---|
| scikit-build-core / meson-python / setuptools / maturin | Can the build backend produce a valid wheel? | Did the generated native/Python interface change incompatibly? |
| pybind11 / nanobind / Cython | Can C++ types and functions be exposed to Python? | Are binding runtime identities and cross-module type contracts still compatible? |
| cibuildwheel | Can the project build and import across the interpreter × platform × arch matrix? | Did any matrix cell lose API, ABI, dependency, or deployment compatibility? |
| auditwheel / delocate / delvewheel | Is the artifact portable, with dependencies bundled correctly? | Did the *repaired* artifact regress relative to the prior release? |
| abi3audit | Is an `abi3` claim internally consistent? | Did the release change CPython ABI, Python API, native ABI, or support floors — abi3 or not? |
| rattler-build / conda-build / conda-forge | Can the package be built, solved, installed, and pinned? | Do declared pins and `run_exports` agree with the binary evidence? |

PEP 517 deliberately defines a small interface for producing wheels/sdists
and has no generic post-build compatibility-audit hook — a good reason *not*
to force abicheck deep into every build backend. It should integrate
primarily at the **artifact and CI layers**, with optional build-system
helpers for richer evidence, mirroring how abi3audit plugged into
cibuildwheel's audit stage rather than into every backend individually.

## What abicheck already offers this ecosystem

This is not all speculative — enough exists today to start real
integrations without waiting on new architecture. (Contrast with the
[SciPy roadmap](scipy-scientific-python-roadmap.md), most of whose gaps are
genuinely unimplemented; several of the ecosystem gaps below are narrower.)

**Ready now:**

- **Wheel and conda-package comparison.** `abicheck compare` accepts wheel,
  `.conda` (v2 zip), legacy conda `.tar.bz2`, RPM, Deb, and plain archive
  inputs (`abicheck/package.py`), and discovers/matches contained native
  binaries.
- **Uniform CPython-extension recognition.** abicheck already treats
  extension modules produced by Cython, pybind11, nanobind, or hand-written
  C uniformly as CPython extensions (`abicheck/python_ext.py`): it inspects
  their imported CPython C-API symbols and can audit an explicit
  `--abi3`/Limited-API floor (`abicheck/stable_abi.py`,
  [G14](plans/g14-stable-abi-subset.md)). That matters because an extension
  module normally exports little beyond `PyInit_<module>` — an
  exported-symbol diff alone would see almost nothing.
- **Python API comparison via `.pyi`.** When a type stub sits next to the
  extension, abicheck statically diffs functions, classes, methods,
  parameters, defaults, and annotations
  (`abicheck/python_api.py`, [G23](plans/g23-python-level-api-diff.md)).
  This is the layer that actually describes the Python API a *particular*
  module exposes — the binding framework's own headers describe the
  framework, not the generated API.
- **Multi-extension / bundled-library analysis.** The bundle layer
  (`abicheck/bundle.py`, [ADR-023](adr/023-bundle-aware-multi-binary-analysis.md))
  detects unresolved sibling imports, provider changes, and cross-library
  signature drift across a wheel containing several extension modules plus
  vendored shared libraries.

**Partially ready:**

- **NumPy C-API evidence.** abicheck detects `_ARRAY_API`/`_UFUNC_API`
  consumption and recovers the compiled `NPY_TARGET_VERSION` (a string
  literal NumPy's own `_import_array()` shim embeds — reliably recoverable
  by a rodata scan). The raw `NPY_ABI_VERSION`/`NPY_API_VERSION` hex
  constants would need disassembly to recover and are deliberately out of
  scope, same reasoning as the header-AST extractor
  ([G26](plans/g26-numpy-capi-envelope.md)).
- **Wheel deployment claims.** Linux `GLIBC_*`/`GLIBCXX_*`/`CXXABI_*` floor
  checks, musllinux/glibc contradictions, macOS deployment targets, and
  wheel-tag/architecture mismatches are implemented
  ([G10](plans/g10-glibc-floor-check.md),
  [G27](plans/g27-wheel-deployment-verification.md)). Windows runtime
  requirements, CPU-ISA baselines, the full platform-library closure policy,
  and CLI auto-derivation from the compared wheel's own tag remain planned.

**Material gaps** (none has a registry entry yet):

1. A first-class pybind11/nanobind binding-ABI provider (§ below).
2. Automatic resolution of the matching previous PyPI/conda artifact.
3. Release-matrix matching and a support-set delta, rather than only
   per-wheel verdicts — the same conceptual gap the SciPy roadmap's §4
   describes, generalized past the scientific stack.
4. Verification of conda `run_exports`/pins against binary evidence.
5. Cython `.pxd`/`__pyx_capi__` support — already planned as
   [G25](plans/g25-cython-api-abi-frontend.md).

## Integration with scikit-build-core

scikit-build-core is a natural first target: it is the common CMake-oriented
backend for pybind11 and nanobind projects, and its own docs explicitly
delegate redistributable-wheel repair to auditwheel/delocate/delvewheel —
the same "not my job" boundary abicheck should respect for build backends in
general.

Two levels of integration, both optional:

**A. A CMake helper for fast PR-time feedback**, run after the extension
target links, that locates the built `.so`/`.dylib`/`.pyd`, optionally
compares it against a committed baseline snapshot, and emits JSON/JUnit for
CTest/CI. This gives target-level signal without making abicheck part of
scikit-build-core's implementation.

**B. An optional build-evidence manifest in wheel metadata.**
scikit-build-core exposes `${SKBUILD_METADATA_DIR}`, installed into the
wheel's `.dist-info` during the actual build (not the metadata-only hook,
since CMake doesn't run there) — a clean place for an optional
`*.dist-info/abicheck.json` recording binding framework/version, runtime ABI
identity, Python ABI tag, `Limited-API` floor, free-threaded status, and
toolchain facts. Such a manifest must never be trusted blindly — abicheck
should verify its fields against the binary wherever possible and report
manifest-vs-binary contradictions as findings in their own right.

The **final** compatibility check must not live only in build-time CMake
output, because repair tools rename/bundle dependencies, rewrite RPATHs, and
retag wheels after the build runs. So: target check for fast dev feedback,
final-wheel check as the authoritative release gate. The same
target-vs-final-artifact split applies unchanged to classic scikit-build,
Meson, and setuptools extensions.

## Integration with cibuildwheel

This is probably the highest-leverage near-term integration. cibuildwheel
has a first-class audit stage (post-repair, `{wheel}`/`{abi3_wheel}`
placeholders) that already defaults to abi3audit for abi3 wheels — exactly
where a single-artifact abicheck audit belongs alongside it, not in place of
it:

```
build → repair (auditwheel/delocate/delvewheel) → abi3audit → abicheck audit → install tests
```

abi3audit stays specialized to abi3 internal-consistency checks. abicheck's
addition is everything abi3audit doesn't cover: inter-release CPython
ABI/Python-API changes, native-library changes, NumPy targeting, deployment
floors, bundled-library topology, and policy aggregation — via the existing
`abicheck compare <previous-wheel> <new-wheel> --format json`, wrapped in a
project's own script to select the matching baseline until automatic
resolution (gap 2 above) exists.

The missing piece is above the per-wheel level: after all cibuildwheel jobs
finish, a **matrix aggregation** step over the complete old/new wheelhouses
could answer questions a single-wheel audit structurally cannot — did a
platform disappear, does one wheel omit an extension module present
everywhere else, did only one OS raise its floor, does the free-threaded
wheel expose a different Python API. This is the same support-set-delta gap
as the ecosystem-gap table's item 3 and the SciPy roadmap's §4; it should
be designed once and shared rather than built twice.

## Integration with rattler-build and conda-forge

Two distinct opportunities:

**A. Testing the actual package artifact.** rattler-build recipes support
script tests and Python import checks; abicheck can run as a package test
or as a post-build CI step. Since conda packages often split runtime,
headers, and debug info across outputs, the strongest comparison should
accept the runtime package plus its matching devel/debug outputs — abicheck
already documents and uses that package shape.

**B. Verifying `run_exports` and pins against observed evidence.** This is
the more differentiated opportunity and has no registry entry. Today,
`run_exports` and global pinnings are declared policy; abicheck could add
*observed* evidence — e.g. a recipe pinning `libfoo >=4.1,<4.2` when binary
comparison shows 4.1→4.8 stays ABI-compatible (pin is unnecessarily tight)
or pinning `>=4.1,<5` when comparison shows a break at 4.9 (pin is
dangerously loose). Conda-forge's `pybind11-abi` metapackage — a global pin
for packages exchanging native pybind11 types across modules — is a
concrete case where abicheck could verify that a package declaring it
actually contains matching, mutually-compatible pybind11 modules, and flag
packages that share native types without declaring it.

## The binding-ABI opportunity: pybind11 and nanobind

A generic ABI checker sees almost nothing in a typical pybind11/nanobind
module — just `PyInit__core`. The compatibility surface that actually
matters lives elsewhere: the Python functions/classes created at module
init (already covered by the `.pyi` diff above), the binding framework's
internal ABI/internals identity, and whether that identity is shared,
per-module, or domain-scoped across the extensions in one process.

- **pybind11** builds an internals key from its internals version and
  platform ABI identity; globally-registered C++ classes can pass instances
  between modules only when that identity matches (compatible pybind11
  version, compiler, and C++ stdlib configuration). pybind11 3.0 bumped this
  relative to 2.13 and recommends rebuilding all participating extensions
  together.
- **nanobind** maintains a *separate* ABI version from its semantic
  version, exposed internally via `abi_tag()` — covering internal
  data-structure version, compiler/platform ABI, C++ stdlib ABI,
  debug/release, stable-ABI mode, and free-threaded mode. `NB_DOMAIN` can
  deliberately scope type-sharing to a named group of extensions. nanobind
  also ships a stub generator, which feeds directly into abicheck's
  existing `.pyi` diff.

A wheel bundling `pkg/_core.so` (pybind11 3.x) and `pkg/_geometry.so`
(pybind11 2.13) can have both modules import fine in isolation while
cross-module passage of bound C++ objects silently breaks — exactly the
kind of release failure import tests and wheel tags don't express.

**Severity here is inherently contextual**, not a flat "ABI changed" call: a
binding-internals change in one isolated extension that exchanges no native
objects is deployment/interop risk at most; several extensions sharing
globally-registered types, or a known downstream consumer of those types, is
a real break; deliberately `module_local`/domain-scoped types may be
unaffected entirely. Treating every binding-framework version bump as a
uniform ABI break would produce exactly the false-positive pattern
abicheck's own FP-rate gate (`scripts/check_fp_rate.py`) is built to catch.

**Proposed provider shape**, rather than scattering framework-specific logic
through the ELF/PE/Mach-O parsers — one `BindingAbiProvider` in the same
style as [ADR-032](adr/032-evidence-extractor-plugin-interface.md)'s
extractor-plugin interface, collecting a normalized surface (framework +
version, runtime ABI identity, domain, stable/free-threaded flags, C++
runtime/toolchain facts, cross-module type-visibility scope) from binary
evidence first, an optional build-emitted manifest second (verified against
the binary), and the existing `.pyi`/embedded-signature path last for the
Python-level API itself. This is a materially larger step than the current
evidence-tier model and should be evaluated against
[ADR-032](adr/032-evidence-extractor-plugin-interface.md) and
[ADR-034](adr/034-managed-runtime-and-non-c-abi-frontends.md) before design,
not layered on ad hoc — the same caution the SciPy roadmap gives its own
provider-model proposal.

## What abicheck should not try to be

Repeating the boundary that motivates all of the above: not a build backend
(scikit-build-core's job), not matrix orchestration (cibuildwheel's job),
not repair (auditwheel/delocate/delvewheel's job), not abi3-specific linting
(abi3audit's job — keep both tools, don't replace it), not conda package
construction (rattler-build's job), not a binding generator (pybind11's/
nanobind's job). The differentiated role is the compatibility-policy engine
none of those tools share with each other today.

## Suggested implementation order

1. **Artifact-layer plumbing that already mostly works.** Document and test
   the existing `abicheck compare <old> <new>` flow against real wheel and
   conda pairs as a cibuildwheel audit-stage step and a rattler-build
   post-build test — no new code, just recipes/examples, closest to the
   "Phase 0" framing in the original review.
2. **`BindingAbiProvider` for pybind11/nanobind** (needs a `UC-*` registry
   entry + plan file) — the single highest-leverage new capability, since it
   is invisible to every other tool in the pipeline.
3. **Release-matrix / support-set delta** — shared machinery with the SciPy
   roadmap's §4, so design once.
4. **Automatic previous-artifact resolution** (PyPI/conda-forge) and
   **`run_exports`/pin verification** — the pieces that turn this from "a
   tool you invoke" into "a check that runs itself."

## Relationship to existing work

| Idea above | Closest existing plan/ADR | Relationship |
|---|---|---|
| Wheel/conda package comparison | `abicheck/package.py`, [ADR-006](adr/006-package-level-comparison.md) | Already implemented; this doc proposes wiring it into cibuildwheel/rattler-build recipes, not new extraction code. |
| CPython-extension recognition, abi3 audit | [G14](plans/g14-stable-abi-subset.md) | Already implemented (`python_ext.py`, `stable_abi.py`); already treats Cython/pybind11/nanobind/C uniformly at the "is this a CPython extension" level. |
| `.pyi` Python API diff | [G23](plans/g23-python-level-api-diff.md) | Already implemented; nanobind's stub generator and a scikit-build-core manifest are both natural new *inputs* to this existing diff, not a new diff engine. |
| Bundle / multi-extension analysis | [ADR-023](adr/023-bundle-aware-multi-binary-analysis.md) | Already implemented; the binding-ABI provider below would add a framework-identity dimension bundle analysis doesn't currently carry. |
| NumPy C-API evidence | [G26](plans/g26-numpy-capi-envelope.md) | Already partial, per that plan's own status note; unchanged by this doc. |
| Wheel deployment-claim verification | [G10](plans/g10-glibc-floor-check.md), [G27](plans/g27-wheel-deployment-verification.md) | Already partial; unchanged by this doc. |
| pybind11/nanobind `BindingAbiProvider` | [ADR-032](adr/032-evidence-extractor-plugin-interface.md) (plugin interface), [ADR-034](adr/034-managed-runtime-and-non-c-abi-frontends.md) (non-C-ABI frontend scope) | New provider; no registry entry yet. Closest in spirit to the SciPy roadmap's `CythonApiProvider`/`NumPyCapiProvider` — same provider-model pattern, different framework. |
| Release-matrix / support-set delta | [G2](plans/g2-build-config-and-bundle.md) (build matrix), [ADR-002](adr/002-multi-binary-release-compare.md), SciPy roadmap §4 | Same gap as the SciPy roadmap's §4, described there in scientific-stack terms; should share a design rather than being solved twice. |
| Automatic previous-artifact resolution | SciPy roadmap §6 | Same gap, same "none yet" status; a resolver built for one should serve both. |
| conda `run_exports`/pin verification | none yet | New; would need the same artifact-resolution/caching layer as the item above. |
| cibuildwheel audit-stage / matrix aggregation | none yet | New CLI/CI surface; the per-wheel half needs no new code (existing `compare`), the matrix-aggregation half does. |

As with the SciPy roadmap, the next step for turning any row above into
real work is a `UC-*` entry in [`usecase-registry.yaml`](usecase-registry.yaml)
with `status: planned` and a `docs/development/plans/gNN-*.md` plan file
following the standard template (Problem · Goal & acceptance criteria ·
Design · Files & surfaces · Tests · Example fixtures · Effort & risk · Out of
scope), per [`plans/index.md`](plans/index.md).
