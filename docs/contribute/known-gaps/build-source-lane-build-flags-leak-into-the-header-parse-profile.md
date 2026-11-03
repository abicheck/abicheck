# build-source lane: build flags leak into the header-parse profile

**Status (2026-11-03): CLOSED** for both shapes; root causes below were
narrower than first recorded:

1. The 12 dump failures came from `_resolve_force_cpp` choosing C++ for a C
   header because of its `#ifdef __cplusplus extern "C"` guard, while the
   compile database supplied a C `-std=`. A C `-std=` now selects C mode
   (`extract/headers/toolchain.py`).
2. The `pass_through_flags` refusals were not per-side build flags in
   general: every one was `-include <the compared header>` (catalog
   `V1_FORCE_INCLUDE v1.h`), fingerprinted by content. Declared public headers
   are no longer re-emitted as forced includes
   (`buildsource/forced_include_keys.py`). No ADR change was needed.

Original report follows.

**Status (2026-11-03): OPEN.** Measured on Linux/GCC, base `1694525ee` and
this branch alike (identical rows): `python tests/validate_examples.py
--artifact-variant build-source` reports 12 ERROR and 7 refused rows that the
`debug-headers` lane passes.

Two shapes, one class (product build settings used as header-analysis
settings — the same class as the case98 dialect refusal closed by the
ADR-050 amendment of 2026-11-03):

1. **C library, C++ header parse, C `-std=`** (12 cases: case28, 30, 33, 35,
   36, 39, 40, 41, 64, 112, 209, 210). The dump parses the C library's header
   in C++ mode while the compile database contributes `-std=gnu11`; castxml
   fails with `invalid argument '-std=gnu11' not allowed with 'C++'`, so the
   dump exits non-zero.
2. **Per-side `pass_through_flags` refuse the pair** (7 cases: case31, 42,
   54, 55, 56, 57, 62). Both dumps succeed, but compile-database flags that
   differ between the old and new builds enter `profile_fingerprint` as
   `pass_through_flags`, and compare refuses with `profile_mismatch`
   (`verdict: null`).

Not yet decided: which build flags may parameterize the header parse at all
(language mode and `-std` must follow the header's language, not a C TU's),
and whether a per-side build-flag difference is a build *fact* for L3
(`abi_relevant_build_flag_changed`) rather than a comparability refusal. Both
change the extraction contract, so they need an ADR-050 ruling.

Separately, case103 (`toolchain_flag_drift`) reports COMPATIBLE_WITH_RISK in
this lane against a COMPATIBLE label; not investigated here.
