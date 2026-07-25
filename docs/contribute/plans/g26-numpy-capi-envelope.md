# G26 — NumPy C-API compatibility-envelope analysis

**Registry:** `UC-TC-numpy-capi-envelope` (`partial`)
**Effort:** L · **Risk:** medium
**Origin:** [SciPy / Scientific-Python Roadmap](../scipy-scientific-python-roadmap.md) §2.

**Status note (delivered scope):** empirical verification against a real
compiled NumPy 2.4 extension (see PR #564's follow-up discussion) confirmed
that `NPY_TARGET_VERSION` — recovered via the human-readable
`NPY_FEATURE_VERSION_STRING` NumPy's own generated `_import_array()` shim
embeds as a literal string — is reliably recoverable via a plain rodata
scan, surviving `strip` and independent of optimisation level. The raw
`NPY_ABI_VERSION`/`NPY_API_VERSION` **hex** constants are not: they are
passed as `PyErr_Format` varargs via a compiler-emitted immediate load, not
a string literal, so recovering them needs disassembly — a new heavy
dependency this project's no-heavy-deps policy rules out (same reasoning
that keeps G4 out of scope). Delivered: consumption detection
(`_ARRAY_API`/`_UFUNC_API` presence), the `NPY_TARGET_VERSION` string, a
two-snapshot delta detector wired into `compare()`, and a standalone
wheel-metadata cross-check (`check_numpy_metadata_contract`, mirroring
G10's `parse_manylinux_glibc_floor` — not auto-wired into the CLI compare
path, same rationale). Deferred, and why, in "Out of scope" below.

## Problem

The NumPy C-API is one of the most important binary contracts in scientific
Python, and abicheck's existing native-symbol analysis does not adequately
describe it: the API is consumed largely through runtime capsule tables
(`_ARRAY_API`, `_UFUNC_API`, populated by `import_array()`/`import_ufunc()`),
not ordinary exported symbols, so a `symbol_removed`/`symbol_added` diff over
the dynamic symbol table sees nothing.

NumPy's own documented compatibility model gives abicheck a well-specified
envelope to check against, but nothing currently extracts or verifies it:

- `NPY_ABI_VERSION` and `NPY_API_VERSION` (compiled-in constants) fix the
  ABI/API generation an extension was built against.
- `NPY_TARGET_VERSION` lets a project pin an *older* target API deliberately
  (so the extension keeps working against older NumPy runtimes even when
  built with a newer NumPy).
- `NPY_FEATURE_VERSION` and `NPY_NO_DEPRECATED_API` gate which API slots are
  legal to call.
- NumPy 2.0 changed the ABI: a wheel built against NumPy 1.x does not work
  against NumPy 2.x; a wheel built against NumPy 2.x *may* work against
  NumPy 1.x depending on the configured target — the "may" is exactly the
  compatibility question a scanner should answer instead of leaving it
  implicit.
- The NumPy version range declared in wheel metadata (`numpy>=1.23.5`) can
  silently understate or overstate what the binary evidence actually
  requires.

Without this, a release comparison cannot say *which NumPy environments were
actually lost or gained* between two builds of the same extension — the
question a release manager actually needs answered.

## Goal & acceptance criteria

- [x] Extract, per extension module, whether it consumes the NumPy C-API at
      all (`_ARRAY_API`/`_UFUNC_API` referenced) and, when it does, the
      `NPY_TARGET_VERSION` it was compiled to target (the minimum NumPy
      runtime its C-API usage requires). The raw `NPY_ABI_VERSION`/
      `NPY_API_VERSION` **hex** constants are not extracted — see the status
      note above; `NPY_NO_DEPRECATED_API` is not a runtime-visible
      string/constant at all (it only gates which functions the *source*
      may call, compiled away with no trace) and is not extracted.
- [x] Cross-reference the declared NumPy version range from wheel/package
      metadata (`*.dist-info/METADATA` `Requires-Dist: numpy`) against the
      binary evidence and flag disagreement
      (`package.parse_wheel_numpy_requirement` +
      `diff_numpy_capi.check_numpy_metadata_contract`).
- [ ] Compute and report a full **support envelope** string ("built with
      NumPy X, target floor Y, verified compatible NumPy Y through 2.x").
      Not delivered as a rendered string — "built with NumPy X" specifically
      needs the un-recoverable raw build-time version (see status note);
      the underlying facts (target floor, declared range, and the
      RISK/BREAKING findings when they disagree) are computed and reported
      as ordinary findings instead.
- [x] Emit new `ChangeKind`s, classified per the root `CLAUDE.md` four-step
      procedure: `numpy_abi_major_incompatible` (`BREAKING`),
      `numpy_target_floor_raised` (`RISK`),
      `numpy_metadata_understates_required_version` (`RISK` — the declared
      floor is a lie relative to the binary evidence),
      `numpy_capi_consumption_added` (`RISK` — not originally listed, added
      during implementation: a module gaining NumPy C-API consumption is a
      new runtime dependency ordinary symbol diffing can't see, the same
      class of gap this whole plan exists to close),
      `numpy_capi_consumption_removed` (`COMPATIBLE`).
      **Deferred** (not implemented — see "Out of scope"):
      `numpy_build_runtime_contract_mismatch`,
      `numpy_deprecated_c_api_reintroduced`,
      `numpy_api_used_above_declared_floor`.
- [x] Degrades honestly for extensions that don't consume the NumPy C-API at
      all (no findings, not false positives — `extract_numpy_capi_surface`
      returns `None`) and for builds where the target-version string isn't
      recoverable (`capi_target_version=None`; both detectors treat this as
      "can't check", not "no floor").

## Design

`abicheck/numpy_capi.py`'s `extract_numpy_capi_surface(binary_path)` scans a
binary's raw bytes (size-capped, format-agnostic — the same marker strings
appear regardless of ELF/PE/Mach-O, so no per-format section parsing is
needed) for NumPy's own generated `_import_array()`/`_import_umath()` shim
literals: `_ARRAY_API`/`_UFUNC_API` presence markers, and the
`NPY_FEATURE_VERSION_STRING`-derived `"(NumPy X.Y)"` target-version string.
Returns `None` (not an empty surface) when neither marker is present — an
ordinary, non-NumPy library produces no finding.

`abicheck/package.py`'s `parse_wheel_numpy_requirement`/
`parse_numpy_requirement_from_metadata` extract the declared `numpy`
version-specifier range from a wheel's `*.dist-info/METADATA`
`Requires-Dist: numpy...` line (skipping marker-gated/optional-extra
entries) as the "declared" side.

`abicheck/diff_numpy_capi.py` has two independent functions:

- `diff_numpy_capi_surfaces(old, new)` — a two-snapshot delta (consumption
  added/removed, target floor raised); wired into `checker.compare()`
  unconditionally, since it needs only the two snapshots' own `numpy_capi`
  field.
- `check_numpy_metadata_contract(surface, declared_numpy_requirement)` — a
  single-artifact self-consistency check (declared range vs. binary
  target). Needs wheel-level metadata `compare()` has no access to per
  library, so — like G10's `package.parse_manylinux_glibc_floor` — this is
  a standalone function for programmatic use, not auto-wired into the CLI
  compare path.

## Files & surfaces

- New `abicheck/numpy_capi.py` (surface model + binary extractor),
  `abicheck/diff_numpy_capi.py` (both detectors),
  `abicheck/change_registry_numpy.py` (new-kind registry entries, split out
  the same way `change_registry_coverage.py` is — `change_registry.py` is
  at its 2000-line cap).
- `abicheck/checker_policy.py` (new `ChangeKind` members) +
  `abicheck/change_registry.py` (splices in `change_registry_numpy.py`).
- `abicheck/model.py` (`numpy_capi` field on `AbiSnapshot`),
  `abicheck/serialization.py` (persist/derive), `abicheck/service.py`
  (`_try_attach_numpy_capi_surface`, called for all three binary formats).
- `abicheck/package.py` (wheel `Requires-Dist: numpy` parsing).
- `abicheck/checker.py` (wires `diff_numpy_capi_surfaces` into `compare()`).

## Tests

- `tests/test_numpy_capi.py` — binary-evidence extraction against synthetic
  byte fixtures reproducing NumPy's real generated shim strings (verified
  against a real compiled NumPy 2.4 extension during development), plus
  `numpy_capi` serialization round-trip.
- `tests/test_diff_numpy_capi.py` — both detector functions (consumption
  added/removed, target floor raised/dropped/unchanged, metadata
  understatement, the 1.x/2.0 ABI-boundary case, malformed specifiers) and
  an end-to-end test through the real `checker.compare()`.
- `tests/test_package.py` — wheel/METADATA requirement parsing (versioned,
  bare/unconstrained, marker-gated, case-insensitive, multiple
  `Requires-Dist` lines).
- Not delivered: a compiled `examples/` fixture pair (would need a real
  numpy install + C compiler in the example-fixture build matrix, unlike
  the fully synthetic unit coverage above) and a rendered "compatible from
  X through Y" support-envelope string (needs the un-recoverable raw build
  version — see the status note).

## Effort & risk

L — one new evidence extractor (format-agnostic, so effectively one scan
rather than one per binary format) plus a metadata cross-check and a family
of new `ChangeKind`s. Medium risk realized as expected: the raw
`NPY_ABI_VERSION`/`NPY_API_VERSION` hex constants' on-disk representation
depends on compiler codegen (an immediate load, not a string literal) and
was **not** reliably recoverable without disassembly — confirmed by
compiling and inspecting a real NumPy 2.4 extension rather than guessing.
The `NPY_TARGET_VERSION` string, by contrast, turned out to be *fully*
reliable (a literal, compiler-preserved, strip-surviving string) — better
than the plan's original "best-effort, may need reduced coverage" framing
anticipated for that specific fact.

## Out of scope

Deferred from the original acceptance criteria, given the hex-constant
extraction limits above:

- `numpy_build_runtime_contract_mismatch`, `numpy_api_used_above_declared_floor`
  — both need the raw `NPY_ABI_VERSION`/`NPY_API_VERSION` hex constants or a
  per-API-slot call-site inventory, neither recoverable via a string scan.
- `numpy_deprecated_c_api_reintroduced` — `NPY_NO_DEPRECATED_API` gates
  which functions the *source* may call at compile time; it leaves no
  runtime-visible trace (string, symbol, or otherwise) in the compiled
  binary at all.
- The rendered support-envelope string ("built with NumPy X, target floor Y,
  verified compatible NumPy Y through 2.x") — "built with NumPy X" needs the
  same un-recoverable raw build-time version.
- A compiled `examples/` fixture pair (synthetic unit fixtures cover the
  same cases; a real numpy+compiler build-matrix fixture is a reasonable
  follow-up, not required to close this pass).

Already out of scope per the original plan: ufunc/gufunc/dtype-loop surface
comparison (tracked separately, see the NumPy ufunc/dtype item in the
[SciPy roadmap](../scipy-scientific-python-roadmap.md#10-numpy-ufunc-gufunc-and-dtype-surface-comparison) —
not yet gap-plan-ified); runtime `import numpy; numpy.__version__` probing
(a hermetic-sandbox concern, deferred alongside G23's runtime-introspection
fallback); non-NumPy array-API implementations (CuPy, JAX, etc.); a
disassembly dependency to recover the raw hex constants (would need a new
heavy dependency this project's policy rules out, same reasoning as G4).
