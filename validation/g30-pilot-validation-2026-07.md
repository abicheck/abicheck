# G30 pilot validation (2026-07-25)

Executes the "Pilot validation plan" section of
`docs/contribute/plans/g30-github-actions-integration-model.md` for real:
install real tools and run real `abicheck` builds/scans/compares, recording
what actually happened — not a simulated or hypothetical run. Every result
below comes from an actual build and an actual `abicheck` invocation in this
environment; nothing is inferred from reading the code. **Scope is mixed,
and deliberately labeled as such below**: the PVXS/Make pilot is a real
*upstream* open-source project (`epics-base/pvxs`, cloned from GitHub) built
from source; the CMake/Bazel/package/cross-compiled/icpx pilots exercise a
synthetic in-repo two-version `libdemo` fixture (a real build/compile/
link/compare each time, just not a real upstream codebase) — sufficient to
validate the build-system integration and ABI-break detection path itself,
but not a substitute for a second real-upstream project. The icpx pilot
additionally reaches real, vendor-shipped SYCL runtime binaries (Intel's
own Unified Runtime adapter plugins) beyond the synthetic fixture, since
those ship with the compiler install itself — see that pilot's section for
what that did and didn't cover, and "Still blocked" for MSVC/PDB.

## Summary

| Pilot | Project | Status | Real ABI break detected? |
|---|---|---|---|
| PVXS (recommended pilot, check-project.yml flow) | Real upstream (`epics-base/pvxs`) | Done | Yes — 1.4.0→1.5.0 `API_BREAK`, 1.5.1→1.5.2 `BREAKING` |
| Make (minimal generic) | Real upstream (EPICS Base + PVXS themselves) | Done | Yes |
| CMake (minimal generic) | Synthetic (`libdemo` fixture) | Done | Yes |
| Bazel (minimal generic) | Synthetic (`libdemo` fixture) | Done | Yes |
| Package-only, `.deb` (minimal generic) | Synthetic (`libdemo` fixture) | Done | Yes |
| Cross-compiled target (aarch64) | Synthetic (`libdemo` fixture) | Done | Yes |
| Second vendor-toolchain project: Intel `icpx`/oneAPI | Synthetic (`libdemo` fixture) + real DPC++ runtime plugins | Done | Yes (C++); real SYCL-plugin detection gap found and fixed |
| Second vendor-toolchain project: MSVC/PDB | — | Still blocked — see below | n/a |

Two real, reproducible product bugs were found and fixed in this same PR:
a `dump`-vs-`compare` scope-fingerprint mismatch, and a `sycl_metadata.py`
UR-adapter detection gap against Intel's current oneAPI 2026.1 runtime —
both after initial triage as out-of-scope follow-ups (see "Finding"
sections below).

## PVXS pilot (the confirmed/recommended pilot)

Built EPICS Base R7.0.8.1 from source (`libevent-dev` was a real, previously
undocumented dependency gap for the `epicsTime.h` ecosystem — installed via
apt), then cloned `epics-base/pvxs` and built four tagged releases
(1.4.0, 1.5.0, 1.5.1, 1.5.2) against it, producing real
`libpvxs.so.<ver>`/`libpvxsIoc.so.<ver>` shared libraries for each.

- `abicheck compare` between the 1.4.0 and 1.5.0 builds (header-scoped,
  `--ast-frontend clang` — no `castxml` in this environment) reproduced the
  `API_BREAK` verdict expected from the project's own prior validation
  history.
- `abicheck compare` between the 1.5.1 and 1.5.2 builds (after fixing a
  self-inflicted snapshot-consistency issue — see "Finding" below)
  reproduced `BREAKING` (1 breaking change: an added field on an
  internal-but-exported type), matching this repo's own prior 1.5.1→1.5.2
  analysis.
- The full G30 pipeline was driven by hand end-to-end, step by step, since
  this environment cannot literally trigger GitHub Actions:
  - A real `.abicheck.yml` (`targets:`/`bundles:`/`profiles:`/`baseline:`)
    validated clean via `abicheck project-targets validate`.
  - A real `build-output.json` for the 1.5.2 candidate validated clean via
    `abicheck build-output validate`.
  - `abicheck run-plan generate` produced a real 2-check run-plan from that
    config.
  - `actions/baseline/build_manifest.py` built a real baseline-set manifest
    from the 1.5.1 dumped snapshots (staged as
    `<output-dir>/<library>.abicheck.json`, matching what
    `actions/baseline/run.sh` itself would produce — the script does not
    read an arbitrary `artifact` path as the snapshot location, only as
    metadata about the original binary).
  - `actions/resolve-baseline` resolved correctly against that manifest on
    a simulated second run.
  - `report_envelope.py` produced a real bootstrap-case report envelope.
  - The analysis-step `compare` itself (baseline snapshot vs. the live
    1.5.2 candidate binary) initially hit the `ScopeMismatchError` finding
    below; re-run using two independently pre-dumped snapshots compared
    file-to-file, it produced the real `BREAKING` verdict above.

## CMake pilot

A synthetic two-version CMake project (`libdemo`) with one deliberate ABI
break (a struct field added to a type reachable from the public API, plus a
signature change) built cleanly via `cmake`/`cmake --build`.
`abicheck compare` against the two `libdemo.so` builds correctly reported
`BREAKING` with the expected `type_field_added`/`func_removed`/`func_added`
findings.

## Make pilot

EPICS Base and PVXS are themselves real, non-trivial GNU Make-based build
systems (not CMake) — building all five projects above (EPICS Base + 4 PVXS
tags) from a real `make`-driven build IS the Make pilot; a separate toy Make
project would have added nothing this didn't already cover. Building PVXS
required `configure/RELEASE.local` pointed at the built EPICS Base tree.

Separately, this session also implemented and unit-tested real `ar`/linker
dry-run-transcript scraping for the Make adapter (ADR-053 D2) — covered by
`tests/test_make_adapter.py`, not by this pilot pass.

## Package-only pilot (`.deb`)

Built `.deb` packages (`dpkg-deb`) for both `libdemo` versions and ran
`abicheck compare` directly against the package files (not the raw `.so`).
This surfaced one real, previously-undocumented environment gap: extracting
a `.deb` using zstd compression requires either the `zstd` CLI or the
`zstandard` Python package — neither was present by default. Installed
`zstd` via apt and re-ran; the package-to-package compare then correctly
fell back to DWARF-only analysis (package/directory compare does not accept
`--ast-frontend`) and reported the same real break as the CMake pilot above.

## Cross-compiled pilot (aarch64)

Installed `gcc-aarch64-linux-gnu`/`g++-aarch64-linux-gnu` and cross-compiled
both `libdemo` versions for `aarch64` (`-DCMAKE_SYSTEM_NAME=Linux
-DCMAKE_SYSTEM_PROCESSOR=aarch64 -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc
-DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++`), producing real ARM64 ELF
shared objects on this x86_64 host (`file` confirms `ELF 64-bit LSB shared
object, ARM aarch64`). `abicheck compare --ast-frontend clang` against the
two cross-compiled binaries (both sides live-dumped in one `compare`
invocation) correctly parsed the ARM64 ELF/DWARF and reported the same real
`BREAKING` verdict (`type_field_added`, `func_removed`/`func_added`,
`soname_bump_recommended`) as the native x86_64 build — confirming
cross-target ELF/DWARF parsing is not host-architecture-dependent.

## Bazel pilot

**Initially marked blocked, then unblocked**: no `bazel`/`bazelisk` `apt`
package is directly installable in this environment (the only apt-available
package, `bazel-bootstrap`, doesn't ship a working `bazel` binary — it pulls
a full JDK + gRPC/protobuf Java chain to *build* bazel from source). Bazel
itself, however, is a real, network-downloadable static binary: `bazelisk`
(the official version-managing launcher) fetched cleanly from its GitHub
releases (`bazelisk-linux-amd64`), and on first invocation transparently
downloaded and ran real Bazel 9.2.0.

Built the same two-version `libdemo` fixture as the CMake pilot (identical
`src/demo.cpp`/`internal.cpp`/`demo.h`, reused verbatim) under a minimal
Bazel `MODULE.bazel`/`BUILD.bazel` (`cc_binary` with `linkshared = True`,
via `rules_cc` — Bazel 9's bzlmod migration removed the native `cc_binary`
rule, so this needed a `load("@rules_cc//cc:defs.bzl", "cc_binary")`, not
just a bare `BUILD.bazel`). `bazel build //:libdemo.so` produced real
`bazel-bin/libdemo.so` outputs for both versions.

`abicheck dump ... --sources <bazel-project-dir> --allow-build-query`
correctly auto-detected the Bazel project (`build_query.detect_build_system`
finding `MODULE.bazel`) and ran a real `bazel aquery` itself (zero-config,
no manual `bazel aquery` invocation needed on the user's part) — the
resulting snapshot's `build_source.build_evidence.generators` records
`kind: "bazel"`, with 2 real `compile_units` and 1 real `link_unit`
(`libdemo.so`, `kind: shared_library`, both objects as inputs) captured from
the actual `aquery` action graph. `abicheck compare` on the two dumps
correctly reported `BREAKING` with the same real findings as every other
`libdemo` pilot (`type_field_added`, `func_removed`/`func_added`), plus a
real `header_parse_context_drift` finding surfaced *because* real Bazel
build-context data was now feeding the compile-flag comparison
("Build-flag & toolchain drift ... from build-system data (compile DB /
CMake / Ninja / Bazel)" showed `[on]` in the evidence-coverage banner,
unlike the header-only CMake/cross-compiled pilots where that check is
`[off]`).

As a bonus check of this PR's own ADR-053 work against a **third** real
build-system evidence source (previously only unit-tested with
captured/mocked `aquery` JSON, never a live `bazel` invocation):
`link_attribution.attribute_sources_to_targets()` run directly over the real
captured `BuildEvidence` correctly attributed both `src/demo.cpp` and
`src/internal.cpp` to `target:////:libdemo.so` via the link-unit-graph
channel (Bazel's own compile-unit → link-unit → terminal-DSO graph), exactly
as ADR-053 D2 documents.

## Second vendor-toolchain pilot: Intel oneAPI (`icpx`) — unblocked; MSVC/PDB remains blocked

**Initially marked fully blocked, then partially unblocked**: contrary to
the initial assessment, Intel's oneAPI compiler is a real, freely
installable `apt` package (`apt.repos.intel.com`, public GPG-signed repo,
no license key needed for the compiler component) — `intel-oneapi-compiler-dpcpp-cpp`
installed cleanly and produced a real, working `icpx` (Intel(R) oneAPI
DPC++/C++ Compiler 2026.1.0).

- **Real vendor-compiled C++ `.so`**: built the same two-version `libdemo`
  fixture with `icpx -shared -fPIC -std=c++17` instead of gcc/clang. The
  binary's `.comment` section (`readelf -p .comment`) confirms genuine
  vendor-toolchain provenance: `Intel(R) oneAPI DPC++/C++ Compiler 2026.1.0
  (2026.1.0.20260617)`, not a substitute. `abicheck compare` (clang L2
  frontend for headers, real icpx-produced DWARF for L1) correctly parsed
  both real icpx-built binaries and reported the same real `BREAKING`
  verdict (`type_field_added`, `func_removed`/`func_added`) as every other
  `libdemo` pilot — confirming ELF/DWARF parsing is toolchain-producer-
  agnostic, not just compiler-family-agnostic (gcc/clang) as the other
  pilots already showed.
- **Real SYCL device-code compilation**: `icpx -fsycl -shared -fPIC` on a
  genuine `sycl::queue`/`parallel_for` kernel compiled and linked cleanly
  (`sycl/sycl.hpp` from the installed DPC++ runtime, no GPU/OpenCL runtime
  needed just to *compile*), producing a real ELF `.so` `abicheck dump`
  parses without error (binary/symbols-level; 4 functions extracted).
- **Real SYCL runtime plugin metadata (`sycl_metadata.py`) — found and
  fixed.** The installed DPC++ runtime ships real Intel Unified Runtime (UR)
  adapter plugins
  (`/opt/intel/oneapi/compiler/2026.1/lib/libur_adapter_{opencl,level_zero}.so.*`).
  Running `abicheck.sycl_metadata.parse_sycl_plugin()` directly against
  these real, current (oneAPI 2026.1) adapters originally returned `None`
  for both — "missing urAdapterGet — not a valid UR adapter". Root cause:
  `nm -D` confirmed neither real adapter exports a symbol named
  `urAdapterGet` at all (25 exported `ur*` symbols total, every one of them
  an `urGet<Category>ProcAddrTable` function-pointer-table getter —
  `urGetAdapterProcAddrTable`, `urGetPlatformProcAddrTable`,
  `urGetDeviceProcAddrTable`, …, no individual per-verb symbol exported
  directly at all). `sycl_metadata.py`'s UR-plugin validity check
  (`parse_sycl_plugin`) hard-required the older `urAdapterGet` symbol, so
  it silently rejected a real, valid, current-generation UR adapter as "not
  a valid UR adapter" — a genuine staleness gap relative to the real
  Unified Runtime ABI's evolution, surfaced only by running against a real,
  currently-shipping vendor runtime (a synthetic/mocked plugin fixture
  would not have caught this, since it would be built to match whatever
  symbol set the detector already expects).

  **Fix**: `parse_sycl_plugin()` now accepts either UR generation — the
  legacy `urAdapterGet` marker or the current `urGetAdapterProcAddrTable`
  marker. `_detect_ur_version_from_symbols()`'s landmark heuristic
  (`urBindlessImages*`/`urVirtualMem*`/`urCommandBuffer*`/`urAdapterGet`)
  only ever matched the legacy per-verb shape to begin with — none of those
  `startswith()` checks match `urGet<Category>ProcAddrTable` names, so it
  already, correctly, returned `""` for the modern shape once the validity
  gate itself was fixed (deliberately left unchanged rather than guessing
  a version from landmarks that are always fully present on every current
  adapter regardless of actual UR release — a real per-release signal
  doesn't exist in the export set for this generation). Verified against
  both real installed adapters directly (`parse_sycl_plugin` now returns a
  populated `SyclPluginInfo` with all 25 real entry points for each) and
  end-to-end via `discover_sycl_plugins`/`parse_sycl_metadata` over the
  real oneAPI lib directory (`implementation: "dpcpp"`, all 3 real backends
  — `level_zero`, `level_zero_v2`, `opencl` — discovered). New regression
  tests in `tests/test_sycl_metadata.py` pin both the validity fix and the
  version-detection non-regression.
- **MSVC/`cl.exe` — still genuinely blocked.** No amount of `apt`/network
  access changes this: MSVC requires a licensed Windows installation and
  is not redistributable for a Linux container the way Intel's compiler
  is. `pdb_parser.py`/PDB-based MSVC ABI parsing remains unvalidated by
  this pilot pass.

## Finding (fixed): `dump`/`compare` disagreed on whether `--header <dir>` implies public-header-dir scope

**Fixed in this PR**, after initially being recorded as a deferred ADR-050
follow-up — the user asked for it to be closed here rather than left as a
documented gap, and the fix turned out to be small and cleanly scoped once
root-caused precisely (see below), so it landed alongside the ADR-053 work
rather than needing its own follow-up PR.

**Symptom.** `abicheck dump lib.so --header include/` followed later by
`abicheck compare that-dump.json lib2.so --header new=include2/` raised
`ScopeMismatchError` ("old and new snapshots do not cover the same declared
surface") even when `include/` and `include2/` name the identical logical
header set — i.e. the exact "resolve a baseline once, then compare it
against each new commit's live binary" pattern the G30 pipeline
(`actions/baseline` → `actions/check-target`) is built around.

**Root cause.** `compute_extraction_contract`'s `scope_fingerprint` hashes
`public_header_dirs` (among other fields). `compare`'s `--header` flag is
documented as doubling as the public-header provenance set — `cli_resolve.
_resolve_compare_snapshots` runs `split_public_header_inputs()` on every
`--header` value and forwards directory entries as `public_header_dirs`.
`dump`'s `--header`/`-H` is a *different*, narrower flag (`dump` has a
separate, explicit `--public-header`/`--public-header-dir` pair for
provenance tagging) — plain `dump --header <dir>` never populated
`public_header_dirs` at all. So a snapshot produced by `dump --header X`
always carried `public_header_dirs=[]`, while the equivalent live side of a
`compare ... --header new=X` call always carried `public_header_dirs=[X]`
— two extractions of the byte-identical header set, fingerprinting as a
scope mismatch purely from which of the two commands produced them.

**The fix (surgical, decoupled from declaration-provenance tagging).**
`dumper.dump()` gained a new `scope_header_dirs` parameter that is folded
into the extraction contract's `public_header_dirs` scope field *only* —
`apply_provenance()`'s call (ADR-015's origin/public-API tagging) still
reads the original, unmodified `public_header_dirs`, so this does **not**
silently start tagging declarations `public_header`/`private_header` for
existing `dump --header`-only callers; that stays exactly as opt-in as
before, via the separate `--public-header`/`--public-header-dir` flags.
`cli_dump_helpers.perform_elf_dump` now computes `scope_header_dirs` from
the raw `-H`/`--header` CLI arguments via the same `split_public_header_
inputs()` helper `compare` already uses, so a directory argument feeds the
scope contract identically regardless of which command produced the
snapshot. Verified against this pilot's own real PVXS 1.5.1/1.5.2 binaries
(re-running the exact previously-failing command below now succeeds and
reproduces the correct `BREAKING` verdict directly, without the two-step
workaround) plus new regression tests
(`tests/test_dumper_contract_wiring.py`,
`tests/test_cli_dump_helpers_coverage.py`) pinning both the scope-agreement
fix and the provenance-tagging non-regression.

```
abicheck dump libpvxs.so.1.5 --header include/          # 1.5.1, no --public-header-dir
  -o baseline-set/libpvxs.abicheck.json
abicheck compare baseline-set/libpvxs.abicheck.json libpvxs.so.1.5 \
  --header new=include/                                  # 1.5.2, live compare-side dump
# before the fix: ScopeMismatchError; after: real BREAKING verdict
```

**Scope note.** This fix covers the ELF `dump`/`compare` CLI path (the one
this pilot's real PVXS scenario exercises and the G30 pipeline's Linux CI
use case). The PE/Mach-O header-scoped dump path
(`service._try_header_scoped_dump`) calls `_attach_extraction_contract`
directly rather than through `dumper.dump()` and was not touched — it
already threads its own `public_header_dirs` straight from that path's
caller, a different (and, for that path, already-consistent) wiring; if a
comparable dump-vs-compare asymmetry is ever found there, it needs its own
targeted look, not an assumption that this fix already covers it.

## What this validates about ADR-053 specifically

None of the pilots above exercised a build system wired into the new
`attribution_path`/`"inferred"` build-output projection end-to-end (that
wiring is explicitly deferred — ADR-053 D5, "CLI/Action pipeline wiring" —
the algorithm, Make link-unit capture, ingest filtering, and
`build_output.py` validator are unit-tested directly, not through a pilot's
CLI surface, since no CLI/Action entry point produces or consumes
`attribution_path` yet). The Bazel pilot does, however, exercise the
underlying `link_attribution.attribute_sources_to_targets()` algorithm
directly against a real, live-captured `bazel aquery` `BuildEvidence` for
the first time (previously only unit-tested against captured/mocked
`aquery` JSON) — a genuine, if narrower, ADR-053 validation beyond CMake and
Make. The pilots above otherwise validate the surrounding G30 pipeline
(config validation, build-output validation, run-plan generation, baseline
resolution, real-project scan/compare correctness across five build-system
shapes and a cross-compiled target, plus a real `dump`/`compare`
comparability-contract bug found and fixed) that ADR-053's future
CLI/Action wiring will sit on top of.
