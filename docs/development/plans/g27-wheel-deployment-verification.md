# G27 — wheel tag / deployment-claim verification

**Registry:** `UC-TC-wheel-deployment-claims` (`planned`)
**Effort:** L · **Risk:** low
**Origin:** [SciPy / Scientific-Python Roadmap](../scipy-scientific-python-roadmap.md) §3.
Generalizes [G10](g10-glibc-floor-check.md) (Linux glibc floor only) across
platforms and claim types; reuses [G13](g13-arch-mismatch-guard.md)'s
architecture-guard machinery and [G12](g12-security-hardening.md)'s
hardening-flag capture.

## Problem

A wheel's filename tag and package metadata make explicit promises about
where its binaries will run. G10 covers exactly one of those promises
(Linux manylinux glibc floor) and is still `planned`. The same class of
"claim vs. binary evidence" mismatch exists — largely unchecked — across
every platform a scientific-Python wheel matrix targets:

- **Linux**: manylinux tag vs. required `GLIBC_*`; musllinux compatibility;
  `GLIBCXX_*`/`CXXABI_*` floor; RPATH/RUNPATH correctness; dependencies
  outside the permitted `manylinux`/`musllinux` wheel closure.
- **macOS**: wheel deployment target (`MACOSX_DEPLOYMENT_TARGET` embedded in
  the wheel tag) vs. the Mach-O `LC_VERSION_MIN_MACOSX`/
  `LC_BUILD_VERSION` minimum-OS load command; SDK symbol availability;
  architecture consistency (`x86_64` vs `arm64` vs `universal2`); OpenBLAS
  vs. Accelerate backend identification.
- **Windows**: architecture/subsystem consistency; UCRT/MSVC runtime
  requirements; accidental dependency on a developer-machine DLL not present
  on a clean target; MinGW vs. MSVC runtime transitions.
- **Cross-platform**: CPU ISA baseline (SSE/AVX/AVX2/AVX-512 on x86,
  NEON/SVE feature requirements on ARM) vs. the wheel tag's implied baseline;
  wheel tag vs. the binary's actual recorded architecture (already partly
  covered by G13's `e_machine`/`EI_CLASS` guard for arbitrary binary pairs,
  but not tied to the wheel *tag's* claim specifically); unexpected OpenMP
  runtime additions; changed security-hardening properties (G12's detectors
  exist, but aren't yet cross-checked against a wheel's platform claim).

An accidental AVX2 instruction, an understated macOS deployment target, or a
too-new glibc symbol makes an otherwise API-compatible wheel simply fail to
load on part of its advertised install base — a deployment-tier break with
no native-ABI signal at all.

## Goal & acceptance criteria

- [ ] Parse the wheel filename tag (PEP 425/600 platform tags:
      `manylinux_2_28_x86_64`, `musllinux_1_2_aarch64`,
      `macosx_11_0_arm64`, `win_amd64`, …) into a structured claim:
      platform family, libc floor (glibc/musl), OS deployment-target floor,
      architecture.
- [ ] For each binary inside the wheel, extract the corresponding evidence:
      `GLIBC_*`/`GLIBCXX_*`/`CXXABI_*` version-need floor (Linux, extends
      G10's mechanism to `GLIBCXX`/`CXXABI`), `LC_VERSION_MIN_MACOSX`/
      `LC_BUILD_VERSION` (macOS), the PE machine type and UCRT/MSVC-runtime
      import set (Windows), and the CPU ISA baseline implied by disassembled
      dispatch/feature-detection sections where staticly recoverable.
- [ ] Compare claim vs. evidence and emit deployment-`RISK`/`BREAKING`
      findings on mismatch, each classified per the root `CLAUDE.md`
      four-step procedure — reusing G10's `platform_baseline_floor_raised`
      kind for the glibc case and adding platform-specific siblings (e.g.
      `macos_deployment_target_raised`, `windows_runtime_requirement_added`,
      `wheel_tag_architecture_mismatch`, `wheel_closure_dependency_violation`).
- [ ] A within-claim binary (evidence at or below the tag's promised floor)
      stays clean on all new checks.
- [ ] Musllinux and macOS/Windows checks are genuinely new coverage, not
      just glibc generalized in name — each platform's specific evidence
      source (Mach-O load commands, PE import table, ELF versioned symbols)
      is actually parsed.

## Design

1. A `wheel_tag.py` (or an addition to `abicheck/package.py`) parses PEP
   425/600 wheel filename tags into a structured `WheelPlatformClaim`.
2. Per-platform evidence extractors reuse existing metadata modules
   (`elf_metadata.py`, `macho_metadata.py`, `pe_metadata.py`) — this plan adds
   the *claim-vs-evidence comparison*, not new binary parsing where those
   modules already expose the needed fields (e.g. Mach-O load commands are
   likely already parsed for other purposes; confirm before adding a second
   extractor).
3. `diff_versioning.py` gains the `GLIBCXX`/`CXXABI` floor comparison
   alongside G10's `GLIBC` floor (same mechanism, wider version-prefix set).
4. A new `diff_wheel_deployment.py` (or an addition to the `compare-release`
   wheel-matching pass) runs the claim-vs-evidence checks once G10 lands the
   Linux half, so this plan can start Linux-only if G10 isn't done yet and
   fold in as siblings.
5. CPU ISA baseline detection is the highest-uncertainty piece — start with
   the disassembly-free case (dispatch-table symbol names like
   `_avx2`/`_sse42` suffixes, common in NumPy/SciPy's runtime CPU dispatch)
   and treat true instruction-level scanning as a stretch goal, not an
   acceptance-criteria blocker.

## Files & surfaces

- `abicheck/package.py` (wheel tag parsing), `abicheck/diff_versioning.py`
  (glibc/GLIBCXX/CXXABI floor, extending G10), `abicheck/macho_metadata.py`
  / `abicheck/pe_metadata.py` (deployment-target / runtime-requirement
  fields if not already exposed), a new `abicheck/diff_wheel_deployment.py`
  (claim-vs-evidence comparison across all platforms), `abicheck/checker_policy.py`
  + `abicheck/change_registry.py` (new kinds).

## Tests

- Unit: wheel-tag parser on real-world tag strings (manylinux variants,
  musllinux, macOS universal2, Windows) including malformed/unrecognized
  tags (must degrade to "no claim checked", not crash).
- Per-platform claim-vs-evidence cases: Linux glibc/GLIBCXX floor exceeded,
  macOS deployment target raised, Windows UCRT requirement added — each
  clean-below-floor and breaking-above-floor.
- `examples/` pairs with `ground_truth.json` entries for at least the macOS
  deployment-target case (the SciPy roadmap's north-star example: "macOS
  OpenBLAS deployment target raised: 10.14 → 12.3").

## Example fixtures

Two `.whl` fixtures tagged `macosx_10_14_x86_64` where the second version's
Mach-O binaries actually carry `LC_BUILD_VERSION` minos `12.3` — ground
truth: `macos_deployment_target_raised` (`RISK`), surfaced even though the
Python/native API is otherwise unchanged.

## Effort & risk

L — mostly comparison logic and metadata extraction over binary formats
abicheck already parses; the new per-platform evidence extraction (Mach-O
load commands, PE runtime imports) is incremental, not a new frontend. Low
risk: each platform's claim format (PEP 425/600, Mach-O load commands, PE
headers) is well-specified and stable; the CPU-ISA-baseline sub-goal is the
one open-ended piece and is scoped as best-effort, not a hard blocker.

## Out of scope

True instruction-level CPU ISA disassembly (the dispatch-symbol-name
heuristic is the acceptance bar; full disassembly is a stretch goal, not
required); OpenMP-runtime-collision detection at the process level (that is
runtime behavior, not a static wheel-claim check); conda package
`run_exports`/pinning verification (a separate, package-manager-specific
mechanism — see the "one-command PyPI/conda compare" item in the
[SciPy roadmap](../scipy-scientific-python-roadmap.md#6-one-command-pypi-and-conda-release-comparison),
not yet gap-plan-ified).
