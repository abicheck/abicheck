# G1 — Cross-platform end-to-end validation (Windows / macOS)

**Registry:** `UC-PLAT-windows-pe` (`partial`), `UC-PLAT-macos-macho` (`modeled`)
**Effort:** L · **Risk:** medium (CI runner availability, toolchain drift)

## Problem

The PE/PDB and Mach-O metadata layers are unit-tested, and main added an
MSVC+PDB end-to-end lane (`tests/test_msvc_pdb_e2e.py`), but the **example
catalog and the core workflows (`compare`, `appcompat`) are not exercised on
native PE/Mach-O binaries in CI**. 20 example cases carry `known_gap` notes
concentrated on Windows/macOS, and macOS ARM64 small-struct / HFA-HVA calling
convention is not tracked at all. So "does abicheck handle a `.dll`/`.dylib`
upgrade end-to-end" is currently answered by extrapolation, not by CI.

## Goal & acceptance criteria

- [ ] A `compare` smoke job on **native PE** binaries (MinGW first, then MSVC)
      runs a curated subset of the catalog and matches `ground_truth.json`.
- [ ] A `compare`/`appcompat` smoke job on **native Mach-O** binaries (x86-64
      and ARM64) runs the same curated subset.
- [ ] macOS ARM64 AAPCS: HFA/HVA aggregates and small-struct register passing
      produce a `VALUE_ABI_TRAIT_CHANGED`/`CALLING_CONVENTION_CHANGED`-class
      finding where the SysV-x86-64 path already does, or the divergence is
      documented as a typed `known_gap`.
- [ ] Each case promoted from aspirational to validated drops its `known_gap`
      and `tests/test_platform_coverage_honesty.py` is updated so macOS/Windows
      counts reflect the newly-validated reality.

## Design

1. **Curate a portable subset** of the catalog that compiles cleanly under
   MinGW/MSVC and Apple clang (start from cases already tagged for the platform
   without a `known_gap`). Drive them through `compare` with `ground_truth.json`
   as the oracle, reusing the autodiscovery harness in
   `tests/test_abi_examples.py` / `tests/test_example_autodiscovery.py`.
2. **Windows:** extend the existing `windows-latest` lane (and the MSVC lane)
   to build the subset with the platform compiler and assert verdicts. Reconcile
   the MSVC-vs-Itanium mangling mismatch already documented for
   `--scope-public-headers` (`abicheck/service.py::_has_matched_public_surface`).
3. **macOS:** add a `macos-latest` lane (Apple clang + castxml from Homebrew).
   Track the castxml `__has_cpp_attribute` Xcode issue (see
   `docs/concepts/limitations.md`) via the shim-header workaround.
4. **ARM64 calling convention:** extend `abicheck/macho_metadata.py` /
   `abicheck/dwarf_advanced.py` value-ABI heuristics to model AAPCS64 HFA/HVA
   and the 16-byte small-struct boundary; reuse `tests/test_macos_arm64_abi.py`
   as the unit anchor.

## Files & surfaces

- CI: `.github/workflows/ci.yml` (new/extended macOS + PE smoke lanes).
- `abicheck/macho_metadata.py`, `abicheck/dwarf_advanced.py` (ARM64 AAPCS).
- `tests/test_abi_examples.py` (parametrize platform-native builds).
- `tests/test_platform_coverage_honesty.py` (relax the strict-subset guard as
  cases get validated).

## Tests

- `@pytest.mark.integration` jobs gated to the relevant runner OS.
- Unit coverage for the ARM64 AAPCS heuristic with synthetic DWARF.

## Example fixtures

Reuse existing cases; add native build recipes to `examples/CMakeLists.txt`.

## Out of scope

Windows ordinal-only DLLs without a name table; two-level-namespace Mach-O
resolution (track separately).
