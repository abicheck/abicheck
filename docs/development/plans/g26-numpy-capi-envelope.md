# G26 — NumPy C-API compatibility-envelope analysis

**Registry:** `UC-TC-numpy-capi-envelope` (`planned`)
**Effort:** L · **Risk:** medium
**Origin:** [SciPy / Scientific-Python Roadmap](../scipy-scientific-python-roadmap.md) §2.

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

- [ ] Extract, per extension module, the NumPy build/target facts it was
      compiled with: `NPY_ABI_VERSION`, `NPY_API_VERSION`,
      `NPY_FEATURE_VERSION`, `NPY_TARGET_VERSION` (when present),
      `NPY_NO_DEPRECATED_API`, and whether `_ARRAY_API`/`_UFUNC_API` are
      referenced at all (i.e. whether the module consumes the NumPy C-API in
      the first place).
- [ ] Cross-reference the declared NumPy version range from wheel/package
      metadata (`*.dist-info/METADATA` `Requires-Dist: numpy`) against the
      binary evidence and flag disagreement.
- [ ] Compute and report a **support envelope** — "built with NumPy X, target
      floor Y, verified compatible NumPy Y through 2.x" — not just a
      pass/fail verdict, so a release comparison can state exactly which
      NumPy environments were dropped or added.
- [ ] Emit new `ChangeKind`s, classified per the root `CLAUDE.md` four-step
      procedure: `numpy_abi_major_incompatible` (`BREAKING`),
      `numpy_target_floor_raised` (`RISK`/`API_BREAK` depending on direction),
      `numpy_metadata_understates_required_version` (`RISK` — the declared
      floor is a lie relative to the binary evidence),
      `numpy_build_runtime_contract_mismatch`,
      `numpy_deprecated_c_api_reintroduced`,
      `numpy_api_used_above_declared_floor`.
- [ ] Degrades honestly for extensions that don't consume the NumPy C-API at
      all (no findings, not false positives) and for builds where the
      relevant constants aren't recoverable (report reduced coverage, per the
      existing scan-coverage-row convention).

## Design

A new `abicheck/numpy_capi.py` extracts a `NumPyCapiSurface` from two
evidence sources, cheapest first:

1. **Binary evidence** — the `NPY_*` version constants are typically baked in
   as read-only data or referenced in error-message strings emitted by
   NumPy's `import_array()` macro expansion; recover what's statically
   visible in the ELF/PE/Mach-O rodata/string tables (same evidence tier as
   existing ELF_ONLY analysis — no header or source needed).
2. **Package metadata** — parse the `numpy` requirement range from
   `*.dist-info/METADATA` (reusing `abicheck/package.py`'s existing wheel
   metadata handling) as the "declared" side of the comparison.

`abicheck/diff_numpy_capi.py` diffs two `NumPyCapiSurface`s (or a single
surface against its own declared metadata, for the "metadata understates
binary evidence" self-consistency check) and emits the new kinds through the
existing `checker_policy.py`/`change_registry.py`/reporter pipeline.

## Files & surfaces

- New `abicheck/numpy_capi.py` (surface model + binary/metadata extractors),
  `abicheck/diff_numpy_capi.py` (detector).
- `abicheck/checker_policy.py` + `abicheck/change_registry.py` (new kinds).
- `abicheck/model.py` (`numpy_capi` field on `AbiSnapshot`),
  `abicheck/serialization.py` (persist/derive).
- `abicheck/package.py` (reuse existing wheel-metadata parsing for the
  declared-range side).

## Tests

- Unit: synthetic binaries/fixtures carrying known `NPY_*` constant strings,
  checked against a declared metadata range — matching, understated, and
  major-incompatible cases.
- Support-envelope computation: given build/target facts, assert the reported
  envelope string matches the expected "compatible from X through Y".
- Round-trip serialization of `numpy_capi`.
- An `examples/` pair with a `ground_truth.json` entry: two builds of the
  same extension where the second raises `NPY_TARGET_VERSION` without a
  corresponding metadata bump.

## Example fixtures

Two versions of a NumPy C-API extension: v1 targets NumPy 1.23 (declared
`numpy>=1.23.5`); v2 is rebuilt against NumPy 2.3 with the target floor
implicitly raised and the metadata left unchanged — ground truth:
`numpy_metadata_understates_required_version` (`RISK`), independent of
whether the native symbol table changed at all.

## Effort & risk

L — one new evidence extractor per binary format (string/rodata scanning is
narrower than a full frontend) plus a metadata cross-check and a small family
of new `ChangeKind`s. Medium risk: the `NPY_*` constants' exact on-disk
representation depends on how NumPy's macros expand for a given compiler/
optimization level, so recovery may be best-effort and needs to report
partial coverage rather than false-negative silently, mirroring G16's
diagnostics-first posture for a similarly toolchain-sensitive extraction.

## Out of scope

Ufunc/gufunc/dtype-loop surface comparison (tracked separately, see the
NumPy ufunc/dtype item in the [SciPy roadmap](../scipy-scientific-python-roadmap.md#10-numpy-ufunc-gufunc-and-dtype-surface-comparison) —
not yet gap-plan-ified); runtime `import numpy; numpy.__version__` probing
(a hermetic-sandbox concern, deferred alongside G23's runtime-introspection
fallback); non-NumPy array-API implementations (CuPy, JAX, etc.).
