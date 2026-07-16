# SciPy / Scientific-Python Roadmap

**Status:** Proposed — vision/roadmap doc. The three highest-priority items
(§1 Cython API/ABI frontend, §2 NumPy C-API envelope, §3 wheel/deployment
verification) are now gap-plan-ified as **G25**, **G26**, and **G27**
(`usecase-registry.yaml` entries; G25/G27 status `planned`, G26 `partial` —
see its plan's "Out of scope" for what's deferred and why,
[plans/g25-cython-api-abi-frontend.md](plans/g25-cython-api-abi-frontend.md) /
[plans/g26-numpy-capi-envelope.md](plans/g26-numpy-capi-envelope.md) /
[plans/g27-wheel-deployment-verification.md](plans/g27-wheel-deployment-verification.md)) —
these are ready to pick up via the normal
[gap-plan process](plans/index.md). The remaining seven items (§4–§10) are
still vision-only; the "Relationship to existing work" section maps each to
the closest existing gap/ADR, and turning one into real work still means
adding a registry entry and a plan file first. Of §0's wheel-foundation
prerequisites, **G9**, **G10**, and **G16** are now done; **G4** (the
libclang header-AST extractor) is still planned — it is a separate, XL-effort,
high-risk undertaking (a new heavy optional dependency and a second full
parser backend), deliberately not bundled with the G9/G10/G16 work.
**Origin:** external roadmap review (feedback captured verbatim below and
lightly reformatted), 2026-07. Recorded here per the pattern in
[`backlog.md` § "Other deferred roadmap items"](backlog.md#other-deferred-roadmap-items) —
this document is the detailed counterpart for the scientific-Python-specific
half of that review; `backlog.md` keeps the short summary and links here.

---

## Overall assessment

abicheck could be exceptionally interesting for scientific Python. The
strongest positioning is not "a C/C++ ABI checker that happens to be written
in Python," but:

> **The compatibility scanner for compiled Python distributions.**

Subjective value assessment:

| Use case | Value today | Potential |
|---|---|---|
| Native C/C++ libraries and conda packages | 8–9/10 | 9/10 |
| General CPython extension wheels | 7/10 | 9/10 |
| SciPy release gating | 5–6/10 | 9+/10 |
| Scientific-Python ecosystem impact analysis | 4/10 | 10/10 differentiator |

The native foundation is already serious: abicheck combines binary, debug,
header, build, and source evidence; handles package and multi-library
comparisons; has policies, snapshots, SARIF, and CI support; and already
recognizes CPython extensions, `abi3`, free-threaded ABI transitions, and
`.pyi`-based Python API changes (G14, G23).

The gap is that the important compatibility surfaces of SciPy are not
primarily ordinary exported ELF/PE/Mach-O symbols.

SciPy combines Python, NumPy, C, C++, Fortran, Cython, Pythran, BLAS, and
LAPACK while supporting multiple Python and NumPy releases. Its wheel matrix
covers manylinux and musllinux, x86-64 and ARM, macOS OpenBLAS and Accelerate
variants with different deployment targets, Windows AMD64 and ARM64, and
free-threaded/prerelease CPython builds.

That complexity makes SciPy both a difficult target and an ideal showcase.

## The most important conceptual shift

For SciPy, a wheel should not be treated as merely a bag of shared
libraries. It is a collection of overlapping compatibility contracts:

1. Python import and call API.
2. CPython extension ABI.
3. NumPy C-API compatibility.
4. Cython compile-time `.pxd` API.
5. Cython runtime capsule ABI.
6. BLAS/LAPACK and Fortran ABI.
7. Vendored dependency topology.
8. Platform, architecture, libc, and CPU support floor.
9. GIL versus free-threaded CPython compatibility.
10. Supported environment matrix: Python × NumPy × platform × architecture × backend.

abicheck's evidence model is a good architecture on which to build this. The
highest-leverage additions are domain-specific evidence providers, not
hundreds more generic C++ change kinds.

## Highest-priority improvements

### 0. Finish the existing wheel foundations

Before expanding scope, three existing gaps should be closed because they
directly affect trustworthy scientific-Python scans.

- **Vendored-library matching — [G9](plans/g9-wheel-vendored-matching.md).**
  `auditwheel` and `delocate` rename bundled libraries with content hashes.
  abicheck currently may interpret every rebuilt dependency as removed and
  re-added, losing the actual dependency delta. The project's own analysis
  found that this can conceal a real vendored SONAME break.
- **Platform-floor verification — [G10](plans/g10-glibc-floor-check.md).**
  Comparing required `GLIBC_*` versions with the manylinux tag is already
  planned. It should then be generalized to musllinux, macOS deployment
  targets, Windows API/UCRT requirements, `GLIBCXX`/`CXXABI`, and CPU
  instruction-set floors.
- **Header frontend robustness — [G16](plans/g16-header-scope-toolchain-robustness.md) /
  [G4](plans/g4-header-ast-extractor.md).** Real-world scanning found 21
  repeated cases in which header-scoped analysis aborted in host system
  headers, preventing public-versus-private surface classification. Moving
  toward a robust libclang frontend is important for projects that provide
  native public headers.

These are less novel than the features below, but they determine whether
users trust the result.

### 1. A first-class Cython API/ABI frontend

This is the clearest number-one feature for SciPy.

SciPy explicitly exposes public Cython APIs through:

- `scipy.linalg.cython_blas`
- `scipy.linalg.cython_lapack`
- `scipy.optimize.cython_optimize`
- `scipy.special.cython_special`

Their declarations live in distributed `.pxd` files. SciPy documents that
downstream code can be compiled against one SciPy version and run against
another; a mismatch can cause an import exception, memory corruption, or a
crash.

SciPy has now built bespoke regression machinery that snapshots
`module.__pyx_capi__` capsule signature strings and fails when an entry
disappears or changes. The test's own rationale names downstream packages
such as scikit-learn and statsmodels.

abicheck should turn that bespoke mechanism into a reusable surface:

```
CythonSurface
  module
  distributed_pxd_declarations
  capsule_exports[name -> signature]
  public_structs_enums_typedefs
  inline_api
  deprecated_entries
  build_variant: LP64 | ILP64 | other
```

It should detect at least:

- `cython_capi_function_removed`
- `cython_capi_signature_changed`
- `cython_pxd_declaration_removed`
- `cython_struct_or_enum_changed`
- `cython_typedef_changed`
- `cython_inline_api_changed`
- `cython_api_removed_without_deprecation`
- `cython_variant_mismatch`

The extractor can use three progressively stronger sources:

1. Static `.pxd` parsing through the Cython compiler API.
2. Generated-C/build manifest extraction.
3. Optional sandboxed import to inspect `__pyx_capi__`.

SciPy's documented policy gives abicheck useful classification rules: adding
declarations is allowed; function changes require deprecation; exposed
structs, enums, and types are effectively final; and public `cdef` classes
are disallowed.

This one feature would make abicheck immediately relevant to SciPy,
scikit-learn, statsmodels, h5py, and many other Cython-heavy projects.

### 2. NumPy C-API compatibility-envelope analysis

The NumPy C-API is arguably the most important binary contract in
scientific Python, and ordinary native symbol analysis does not adequately
describe it because the API is largely accessed through runtime capsule
tables.

abicheck should extract and reason about:

- NumPy version used at build time.
- `NPY_ABI_VERSION`.
- `NPY_API_VERSION`.
- `NPY_FEATURE_VERSION`.
- `NPY_TARGET_VERSION`.
- `NPY_NO_DEPRECATED_API`.
- Whether `_ARRAY_API` and `_UFUNC_API` are consumed.
- The minimum NumPy runtime implied by used API slots.
- The NumPy range declared in wheel metadata.

NumPy's documented compatibility rules make this especially valuable. It
allows projects to select an older target API with `NPY_TARGET_VERSION`;
NumPy 2.0 changed the ABI; wheels built against NumPy 1.x do not work with
NumPy 2.x, while wheels built against NumPy 2.x may work on NumPy 1.x
depending on the configured target.

Useful findings would include:

```
numpy_abi_major_incompatible
numpy_target_floor_raised
numpy_metadata_understates_required_version
numpy_build_runtime_contract_mismatch
numpy_deprecated_c_api_reintroduced
numpy_api_used_above_declared_floor
```

The important output is not just a verdict. It should compute a support
envelope such as:

```
Built with: NumPy 2.3
C-API target: NumPy 1.23
Declared runtime: numpy>=1.23.5
Verified envelope: NumPy 1.23.5 through 2.x
```

Then a release comparison can say exactly which NumPy environments were
lost.

### 3. Wheel tag and deployment-claim verification

A wheel's filename and metadata make promises. abicheck should verify those
promises against every contained binary.

**Linux:**

- manylinux tag versus required `GLIBC_*`.
- `GLIBCXX_*` and `CXXABI_*` floor.
- musllinux compatibility.
- accidental dependencies outside the permitted wheel closure.
- RPATH/RUNPATH correctness.
- auditwheel-hashed dependency pairing.

**macOS:**

- wheel deployment target versus Mach-O minimum OS commands.
- SDK symbol availability.
- architecture consistency.
- OpenBLAS versus Accelerate variant identification.
- vendored libgfortran compatibility.

**Windows:**

- architecture and subsystem.
- UCRT/MSVC runtime requirements.
- Windows API-set or minimum-OS drift.
- accidental dependency on developer-machine DLLs.
- MinGW versus MSVC runtime transitions.

**All platforms:**

- CPU ISA baseline: SSE/AVX/AVX2/AVX-512, ARM feature requirements.
- wheel tag versus actual machine architecture.
- unexpected OpenMP runtime additions.
- changed security-hardening properties.

This matters directly to SciPy because its wheel workflow intentionally
builds distinct manylinux, musllinux, macOS backend/deployment-target,
Windows architecture, and free-threaded configurations.

An accidental AVX2 instruction, an understated macOS deployment target, or a
too-new glibc symbol can make an otherwise API-compatible wheel unusable.

### 4. Release-matrix parity and a generalized compatibility envelope

abicheck currently has useful build-matrix machinery, but scientific Python
needs a higher-level artifact-matrix model.

The unit of analysis should be an entire release:

```
Python version
× regular/free-threaded CPython
× NumPy floor
× operating system
× architecture
× libc/platform floor
× BLAS backend
× LP64/ILP64
```

The scanner should compare sibling wheels and ask:

- Is the Python API identical across platforms?
- Is the Cython API identical?
- Are all expected extension modules present?
- Is an API missing only on Windows ARM64?
- Does the free-threaded wheel expose the same surface as the regular wheel?
- Are OpenBLAS and Accelerate variants equivalent at the Python level?
- Are platform-specific differences explicitly permitted by policy?
- Was support for an environment dropped between releases?

Rather than only returning a worst-case verdict, it should produce a
support-set delta:

```
Dropped:
  Python 3.12
  NumPy 1.26–1.27
  macOS 10.14–12.2
  x86-64 CPUs without AVX2
Unchanged:
  Python API
  Cython capsule API
  Linux aarch64 support
Added:
  Windows ARM64
  CPython free-threaded 3.14
```

This would make the report much more useful than a flat
`COMPATIBLE_WITH_RISK`.

### 5. Automatic downstream-impact analysis

Detection becomes much more compelling when the report answers: **what
actually breaks, and who consumes it?**

abicheck already has an `appcompat` concept (`abicheck/appcompat.py`,
[ADR-005](adr/005-application-compat-check.md)). Extend that into a
scientific-Python consumer graph:

- Native undefined-symbol dependencies.
- Cython capsule imports.
- `.pxd` `cimport` relationships.
- NumPy C-API requirements.
- CPython ABI requirements.
- Optional Python import/name usage.

For a proposed SciPy change, the report might say:

```
scipy.linalg.cython_blas.dgemm signature changed
Known affected wheels:
  package A 2.1 — imports the old capsule signature
  package B 5.4 — compiled against the old LP64 declaration
Known unaffected:
  package C — does not import dgemm
```

This separates:

- a theoretical break,
- a break in a formally public surface,
- and a break known to affect released downstream artifacts.

That is potentially the largest ecosystem-level differentiator.

### 6. One-command PyPI and conda release comparison

Technical coverage alone will not drive adoption. The normal workflow should
require almost no setup:

```bash
abicheck wheel check dist/scipy-*.whl \
    --against pypi:previous \
    --profile scipy
```

or:

```bash
abicheck package check scipy-1.18.0-*.conda \
    --against conda-forge:previous
```

The command should:

1. Resolve the matching previous artifact.
2. Match platform, architecture, Python ABI, and build variant.
3. Cache downloads and snapshots by content hash.
4. Unpack and normalize repaired/vendored libraries.
5. Run native, CPython, NumPy, Cython, Python API, and platform checks.
6. Emit a PR summary plus JSON/SARIF.
7. Persist an optional `*.dist-info/abicheck.json` manifest.

For conda-forge, a future mode could compare findings with `run_exports` and
package pinning, then recommend whether a migration/rebuild is needed.

### 7. An optional, hermetic runtime-surface provider

A static-first security model is right ([ADR-021b](adr/021-mcp-security-model.md)),
but important Python extension surfaces are only reliably visible after
import.

Add an explicit provider such as:

```bash
abicheck scan wheel.whl --runtime-surface sandbox
```

Run imports in an isolated subprocess or container with:

- no network,
- read-only package tree,
- sanitized environment,
- resource and time limits,
- explicit matching Python/NumPy runtime,
- machine-readable result transport.

It could extract:

- `__pyx_capi__`.
- `__all__`.
- `inspect.signature`.
- `__text_signature__`.
- module and class members.
- NumPy ufunc metadata.
- exported dtypes.
- module GIL declarations.
- package build configuration.

The project's current Python API plan ([G23](plans/g23-python-level-api-diff.md))
deliberately leaves runtime introspection and docstring fallback as future
work, so this is a natural additive provider rather than a redesign.

Static analysis should remain the default. Runtime results should carry
explicit evidence and reproducibility labels.

### 8. A scientific-native profile for BLAS, LAPACK, Fortran, OpenMP, and CPU dispatch

A `scientific-python` or `scipy` policy profile ([ADR-010](adr/010-policy-profile-system.md))
should understand numerical native stacks.

It should recognize:

- LP64 versus ILP64 BLAS integer ABI.
- BLAS/LAPACK symbol mangling and suffix conventions.
- OpenBLAS, Accelerate, MKL, BLIS, FlexiBLAS, or reference BLAS.
- Threading backend changes: pthreads, OpenMP, TBB.
- Multiple incompatible OpenMP runtimes in one process.
- libgfortran and libquadmath SONAME drift.
- Fortran compiler and calling-convention changes.
- hidden character-length argument conventions.
- default integer and logical widths.
- compiler runtime changes.
- CPU baseline and dispatched implementations.

SciPy's own Cython ABI test generator already treats ILP64 as a distinct ABI
configuration for `cython_blas` and `cython_lapack`.

Classification should distinguish:

- **Breaking:** LP64 ↔ ILP64 for the same advertised interface.
- **Deployment risk:** OpenBLAS version or Fortran runtime changed.
- **Behavior/performance risk:** backend or threading implementation changed.
- **Compatible:** internal backend update with unchanged public contract.

### 9. Deprecation-aware historical policy

A two-version diff cannot tell whether a removal was legitimate.

abicheck should optionally consume a chain of release baselines and
maintain lifecycle metadata:

```
introduced
deprecated
scheduled_for_removal
removed
```

SciPy's policy requires a public API to be deprecated, documented in
release notes, and generally retained for at least six months, which
usually means two releases. Cython APIs follow the same policy.

The scanner could then distinguish:

```
python_api_removed_without_deprecation       ERROR
cython_api_removed_too_early                 ERROR
deprecated_api_removed_after_policy_window   EXPECTED
deprecated_api_still_present                 INFORMATION
```

For SciPy's Cython API, it could recognize `deprecate_cython_api` wrappers
and track the release named in the warning.

### 10. NumPy ufunc, gufunc, and dtype surface comparison

This is not strictly native ABI, but it is highly relevant to scientific
Python.

For exported ufuncs and gufuncs, record:

- object name,
- `nin` and `nout`,
- gufunc core signature,
- supported type loops,
- input/output dtype pairs,
- identity,
- casting behavior metadata,
- whether object loops are available.

A function can remain importable with the same Python signature while
dropping support for float32, complex values, or a gufunc shape signature.
That is a meaningful compatibility regression.

These should generally be classified as `API_BREAK` or
`COMPATIBLE_WITH_RISK`, not native `BREAKING`, unless a project policy
explicitly treats dtype-loop stability as a hard contract.

## Recommended architecture

Avoid embedding all of this directly into the core binary parser. Introduce
a common provider interface:

```
SurfaceProvider
  identify(artifact)
  collect(artifact, context) -> SurfaceFacts
  compare(old, new, policy) -> Changes
  coverage() -> CoverageRecord
```

Potential providers:

```
NativeAbiProvider
CPythonAbiProvider
CythonApiProvider
NumPyCapiProvider
PythonApiProvider
WheelMetadataProvider
PlatformFloorProvider
BlasFortranProvider
UfuncSurfaceProvider
RuntimeSandboxProvider
```

Every finding should carry dimensions such as:

```
domain: native_abi | cython_abi | numpy_abi | python_api | deployment | numerical
consumer_action: link | import | compile | call | execute
affected_environments
affected_consumers
evidence_sources
confidence
public_surface_status
```

This is more scalable than continuing to grow a flat registry of change
names without richer grouping. This is a materially larger architectural
step than the existing evidence-tier model (`EvidenceTier` in
`checker_policy.py`) and the plugin extractor interface
([ADR-032](adr/032-evidence-extractor-plugin-interface.md)) — it should be
evaluated against both before implementation, not layered on top
ad hoc.

## A compelling SciPy report

A useful north-star report would look approximately like this:

```
SciPy wheel release compatibility: 1.17.x → 1.18.x

Native ABI
  No public native ABI breaks

Cython ABI
  BREAKING: scipy.linalg.cython_blas.foo signature changed
  Known affected downstream wheels: 3

NumPy C-API
  Target floor unchanged: NumPy 1.26
  Runtime metadata agrees with binary evidence

Python API
  API_BREAK: parameter "tol" removed without completed deprecation period

Wheel deployment
  Linux manylinux tag valid
  macOS OpenBLAS deployment target raised: 10.14 → 12.3
  Windows ARM64 surface matches AMD64
  x86-64 CPU baseline unchanged

Vendored stack
  libgfortran unchanged
  OpenBLAS updated; ABI-compatible
  No new OpenMP runtime

Free-threaded build
  Same public Python and Cython surface as regular CPython build

Overall
  BREAKING for known Cython consumers
  Support envelope narrowed on macOS
```

That is a report a release manager can act on immediately.

## What abicheck should not promise

It should remain explicit that it cannot statically certify:

- numerical accuracy,
- algorithmic behavior,
- floating-point reproducibility,
- race freedom,
- performance stability,
- complete free-threaded safety,
- absence of every possible import-time side effect.

The project already frames runtime instrumentation and behavioral analysis
as outside its core static role (see [Goals § Non-goals](goals.md#non-goals)).

Those areas can be addressed by advisory contract probes, but they should
not be conflated with proven ABI compatibility.

## Best implementation sequence

**Phase 1: trustworthy wheel analysis.** Complete G9 vendored matching, G10
platform floors, and G16/G4 header robustness. Add automatic
previous-release resolution and wheel-tag verification.

**Phase 2: scientific-Python MVP.** Implement Cython `.pxd` plus capsule
surfaces, NumPy C-API targeting, deprecation-aware policies, and a
scientific-python profile. Dogfood the complete flow on SciPy's existing
Cython ABI baselines.

**Phase 3: ecosystem differentiation.** Add release-matrix parity,
downstream impact analysis, hermetic runtime extraction, BLAS/Fortran
intelligence, ufunc/dtype surfaces, and conda rebuild/pinning integration.

The shortest path to making abicheck ecosystem-critical is therefore:

> **Cython contract support + NumPy ABI-envelope support + trustworthy
> wheel-level comparison.**

Those three additions would transform the project from a strong native ABI
tool into something unusually well matched to the actual compatibility
problems faced by SciPy and compiled scientific Python.

## Relationship to existing work

| Idea above | Closest existing plan/ADR | Relationship |
|---|---|---|
| §0 Wheel foundations | [G9](plans/g9-wheel-vendored-matching.md) ✅, [G10](plans/g10-glibc-floor-check.md) ✅, [G16](plans/g16-header-scope-toolchain-robustness.md) ✅ / [G4](plans/g4-header-ast-extractor.md) (still planned, XL) | G9, G10, and G16 are now done. G4 (the libclang frontend) remains the large, separate, high-risk piece — a new heavy optional dependency and a second full parser backend, deliberately not attempted alongside G9/G10/G16. |
| §1 Cython API/ABI frontend | **[G25](plans/g25-cython-api-abi-frontend.md)** (`UC-ARCH-cython-api`, `planned`) | Gap-plan-ified. Same shape as G23's `.pyi` surface work; narrower than [ADR-034](adr/034-managed-runtime-and-non-c-abi-frontends.md)'s general non-native-language scope. |
| §2 NumPy C-API envelope | **[G26](plans/g26-numpy-capi-envelope.md)** (`UC-TC-numpy-capi-envelope`, `partial`) | Consumption detection, NPY_TARGET_VERSION extraction, and the wheel-metadata cross-check are done; the raw NPY_ABI_VERSION/NPY_API_VERSION hex constants need disassembly to recover (out of scope, same reasoning as G4) — see the plan's "Out of scope". New provider; extends the existing CPython-extension recognition ([G14](plans/g14-stable-abi-subset.md)) to NumPy's own capsule-based API. |
| §3 Wheel/deployment verification | **[G27](plans/g27-wheel-deployment-verification.md)** (`UC-TC-wheel-deployment-claims`, `planned`) | Gap-plan-ified. Generalizes [G10](plans/g10-glibc-floor-check.md) across platforms/toolchains; reuses [G13](plans/g13-arch-mismatch-guard.md)/[G12](plans/g12-security-hardening.md) machinery. |
| §4 Release-matrix parity | [G2](plans/g2-build-config-and-bundle.md) (build matrix), [ADR-002](adr/002-multi-binary-release-compare.md) | Extends multi-binary release compare from "verdict" to "support-set delta." |
| §5 Downstream-impact analysis | `abicheck/appcompat.py`, [ADR-005](adr/005-application-compat-check.md) | Extends existing app-compat checking into a scientific-Python consumer graph. |
| §6 One-command PyPI/conda compare | none yet | New CLI surface; would need a package-resolution/caching layer not currently in scope. |
| §7 Hermetic runtime-surface provider | [G23](plans/g23-python-level-api-diff.md) (deferred runtime fallback), [ADR-021b](adr/021-mcp-security-model.md) (sandboxing posture) | Picks up G23's explicitly-deferred runtime-introspection path. |
| §8 BLAS/LAPACK/Fortran profile | [ADR-010](adr/010-policy-profile-system.md) (policy profiles) | New profile + new fact extraction (LP64/ILP64, OpenMP runtime IDs) the profile system doesn't yet have inputs for. |
| §9 Deprecation-aware policy | none yet | Needs multi-baseline history, which the current two-snapshot `compare` model doesn't carry. |
| §10 ufunc/gufunc/dtype surface | none yet | New provider alongside NumPy C-API envelope (§2); explicitly out of scope for G26 itself (see that plan's "Out of scope"). |

§1–§3 have registry entries (G25/G26/G27, above) and are ready to pick up.
The remaining rows are still vision-only. The next step for any of them is
to add a `UC-*` entry to [`usecase-registry.yaml`](usecase-registry.yaml)
with `status: planned` and a `docs/development/plans/gNN-*.md` plan file
following the existing template (Problem · Goal & acceptance criteria ·
Design · Files & surfaces · Tests · Example fixtures · Effort & risk · Out of
scope), per the process in [`plans/index.md`](plans/index.md).
