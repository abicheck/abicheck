# G25 — Cython API/ABI frontend (`.pxd` + capsule surface)

**Registry:** `UC-ARCH-cython-api` (`planned`)
**Effort:** XL · **Risk:** medium
**Origin:** [SciPy / Scientific-Python Roadmap](../scipy-scientific-python-roadmap.md) §1.

## Problem

Several scientific-Python packages expose a **Cython-level** compatibility
contract that native C-ABI analysis (G14) and the `.pyi`-based Python API
check (G23) both miss entirely:

- **Compile-time**: distributed `.pxd` files (e.g. `scipy.linalg.cython_blas`,
  `scipy.linalg.cython_lapack`, `scipy.optimize.cython_optimize`,
  `scipy.special.cython_special`) let downstream Cython code `cimport` types,
  structs, enums, and function declarations and compile against them.
- **Runtime**: Cython modules export a **capsule table**
  (`module.__pyx_capi__`) mapping a C function name to a versioned signature
  string, resolved via `cython.cimports`/`__pyx_capi__` lookup at import time
  rather than through the dynamic symbol table. A mismatch between the
  signature a consumer was compiled against and the signature the provider now
  exports causes an import exception, a silent wrong-arity call, memory
  corruption, or a crash — not a link-time or `dlopen`-time failure abicheck's
  existing detectors would catch.

SciPy has already built bespoke regression machinery
(`scipy/_lib/tests/test_public_api.py`-adjacent Cython API tests) that
snapshots `__pyx_capi__` signature strings and fails when an entry disappears
or changes, specifically because downstream packages (scikit-learn,
statsmodels, and others) compile against these capsules. abicheck should turn
that project-specific mechanism into a reusable, general surface — the same
role G23 plays for `.pyi`-based Python APIs.

Two builds can be C-ABI-identical (same `PyInit_*` export, same imported
`Py*` symbols, same `abi3` tag) and Python-API-identical (`.pyi` unchanged, if
one even exists) while still breaking every Cython consumer, because the
break lives in the capsule signature string and the `.pxd` declaration, which
are not part of either existing surface model.

## Goal & acceptance criteria

- [ ] Extract a `CythonSurface` per module: the module's distributed `.pxd`
      declarations (functions, structs, enums, typedefs, inline API), its
      `__pyx_capi__` capsule exports (`name -> signature string`), and — where
      derivable — a `build_variant` tag (e.g. `LP64`/`ILP64` for BLAS/LAPACK
      wrapper modules, since SciPy's own Cython ABI test generator already
      treats these as distinct ABI configurations for `cython_blas`/
      `cython_lapack`).
- [ ] Diff two surfaces and emit new `ChangeKind`s, each classified per the
      root `CLAUDE.md` four-step procedure:
      `cython_capi_function_removed`, `cython_capi_signature_changed`,
      `cython_pxd_declaration_removed`, `cython_struct_or_enum_changed`,
      `cython_typedef_changed`, `cython_inline_api_changed`,
      `cython_api_removed_without_deprecation`, `cython_variant_mismatch`
      (plus the corresponding `*_added` kinds where meaningful).
- [ ] Works from **static** sources first, mirroring G23's cheapest-safest
      ordering:
      1. `.pxd` parsing via the Cython compiler API (primary — the analog of
         header/`.pyi` diffing).
      2. Generated-`.c`/build-manifest extraction of the `__pyx_capi__` table
         literal (no import required).
      3. Optional sandboxed import to read `__pyx_capi__` directly — deferred,
         opt-in, and reuses whatever sandboxing posture G23's runtime fallback
         and [ADR-021b](../adr/021-mcp-security-model.md) settle on; **not**
         required for the acceptance criteria above.
- [ ] Complements, does not replace, G14 (native C-ABI) and G23 (Python API):
      a single `compare`/`scan` surfaces all three where present.
- [ ] Degrades honestly when Cython is not installed or a module has no
      capsule/`.pxd` surface — report what was recovered (consistent with the
      G23 precedent of reporting partial coverage rather than false-negative
      silently).

## Design

A new `abicheck/cython_api.py` builds the `CythonSurface` (attached to
`AbiSnapshot`, alongside `python_ext`/`python_api` from G14/G23), sourced by:

1. `Cython.Compiler` (if importable) parsing `.pxd` files found alongside the
   package — same "optional dependency, degrade honestly" posture the header
   AST extractors (G4) already use for `libclang`.
2. A regex/literal-table scan of Cython-generated `.c`/`.cpp` output (or the
   built extension's embedded capsule-table initializer) for the
   `__pyx_capi__` signature strings, when the generated source or a build
   artifact is available — this needs no interpreter import.

`abicheck/diff_cython_api.py` diffs two `CythonSurface`s; new kinds route
through the existing `checker_policy.py`/`change_registry.py`/reporter
machinery, following the same wiring G23 used.

Classification follows SciPy's own documented Cython API policy as a
starting default (overridable via policy profile, [ADR-010](../adr/010-policy-profile-system.md)):
adding declarations is `COMPATIBLE`; a capsule signature change without a
completed deprecation window is `API_BREAK`/`BREAKING`; removing an exposed
struct/enum/typedef is `API_BREAK`; a public `cdef class` is out of scope
(SciPy's policy disallows it, so there is no ground truth to diff against
yet).

## Files & surfaces

- New `abicheck/cython_api.py` (surface model + `.pxd`/capsule-table
  extractors), `abicheck/diff_cython_api.py` (detector).
- `abicheck/checker_policy.py` + `abicheck/change_registry.py` (new kinds).
- `abicheck/model.py` (`cython_api` field on `AbiSnapshot`),
  `abicheck/serialization.py` (persist/derive).
- Reuse `abicheck/reporter.py`; surfaced through the existing `compare`/`scan`
  commands — no new top-level command, matching the G14→`scan --abi3` and
  G22 CLI-consolidation precedent.

## Tests

- Unit: `.pxd` pairs exercising each kind (removed function declaration,
  changed capsule signature, removed struct field, changed typedef).
- A synthetic `__pyx_capi__` table-literal fixture (no real Cython build
  required) proving the capsule-signature extractor and diff independently of
  `.pxd` parsing.
- Round-trip serialization of `cython_api`.
- An `examples/` pair with a `ground_truth.json` entry: a capsule signature
  change that G14 (C-ABI) and G23 (Python API) both score `COMPATIBLE`.

## Example fixtures

Two versions of a small Cython extension exposing one `cdef api` function via
`__pyx_capi__`; v2 changes an argument type in the capsule signature string
while the Python-level wrapper and the native export table stay identical —
ground truth: `cython_capi_signature_changed` (`API_BREAK`/`BREAKING`
depending on deprecation state), while G14/G23 checks stay clean.

## Effort & risk

XL — a new frontend (Cython-aware parsing, a second extraction path off
generated C), a family of new `ChangeKind`s, and example fixtures requiring a
real or synthetic Cython build. Medium risk: `.pxd` syntax is well-specified
and the Cython compiler API is stable, but capsule-table extraction from
generated C is format-specific to the Cython version that produced it and
needs to degrade honestly across Cython releases rather than silently missing
entries.

## Out of scope

Runtime behavioral verification of capsule-exported functions (this is a
signature/declaration diff, not a call-compatibility fuzzer); pybind11/
nanobind capsule-like mechanisms (different embedding, not covered by this
plan); public `cdef class` surfaces (SciPy's own policy treats these as
disallowed, so there is no target surface to model yet).
