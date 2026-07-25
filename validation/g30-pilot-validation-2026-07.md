# G30 pilot validation (2026-07-25)

Executes the "Pilot validation plan" section of
`docs/contribute/plans/g30-github-actions-integration-model.md` for real:
clone real projects, install real tools, run real `abicheck` scans/compares,
and record what actually happened — not a simulated or hypothetical run.
All builds and scans below were performed against real cloned/compiled
sources in this environment; nothing here is inferred from reading the code.

## Summary

| Pilot | Status | Real ABI break detected? |
|---|---|---|
| PVXS (recommended pilot, check-project.yml flow) | Done | Yes — 1.4.0→1.5.0 `API_BREAK`, 1.5.1→1.5.2 `BREAKING` |
| CMake (minimal generic) | Done | Yes |
| Make (minimal generic) | Done (via EPICS Base + PVXS themselves) | Yes |
| Package-only, `.deb` (minimal generic) | Done | Yes |
| Cross-compiled target (aarch64) | Done | Yes |
| Bazel (minimal generic) | Blocked — see below | n/a |
| Second complex/vendor-toolchain project | Blocked — see below | n/a |

One real, reproducible product bug was found along the way (not fixed in
this pass — out of scope for ADR-053/G30 P2, see "Finding" below).

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

## Blocked: Bazel pilot

No `bazel`/`bazelisk` package is directly installable in this environment.
The only apt-available package is `bazel-bootstrap`, which does not ship a
working `bazel` binary — it pulls a full JDK + gRPC/protobuf Java dependency
chain to *build* bazel from source, disproportionate cost for a pilot
validation pass. The Bazel adapter (`adapters/bazel.py`) already has unit
coverage against captured/mocked `bazel aquery`/`cquery` JSON in
`tests/`; this pilot could not add a real end-to-end `bazel`-driven build on
top of that in this environment. Genuinely deferred, not attempted further.

## Blocked: second complex/vendor-toolchain pilot

No Intel oneAPI (`icpx`)/SYCL toolchain or MSVC/`cl.exe` is available in
this Linux container, and neither can be installed without external
licensed-installer access this environment does not have. Consistent with
this repo's own prior assessment (referenced in the G30 backlog survey),
this pilot remains genuinely blocked rather than attempted with a
substitute — a substitute toolchain would not exercise the vendor-specific
code paths (`sycl_metadata.py`, `pdb_parser.py`/PDB-based MSVC ABI) this
pilot exists to validate.

## Finding: `dump`/`compare` disagree on whether `--header <dir>` implies public-header-dir provenance

**Not fixed in this pass** — a distinct ADR-050 (comparability contract)
issue, out of scope for ADR-053/G30 P2's TU→DSO attribution work this PR
otherwise covers. Recorded here as a real pilot finding for follow-up.

**Symptom.** `abicheck dump lib.so --header include/` followed later by
`abicheck compare that-dump.json lib2.so --header new=include2/` raises
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
provenance tagging) — plain `dump --header <dir>` never populates
`public_header_dirs` at all. So a snapshot produced by `dump --header X`
always carries `public_header_dirs=[]`, while the equivalent live side of a
`compare ... --header new=X` call always carries `public_header_dirs=[X]`
— two extractions of the byte-identical header set, fingerprinting as a
scope mismatch purely from which of the two commands produced them.

**Workaround used for this pilot's PVXS second-run analysis step**: dump
both sides independently via `abicheck dump` (never `compare`'s live-dump
path) and then `abicheck compare old.json new.json` on the two pre-dumped
files — both sides then go through the same `public_header_dirs=[]`
convention and compare cleanly.

**Suggested follow-up** (not designed or implemented here): reconcile
`dump`'s and `compare`'s `--header`-to-public-header-dir semantics — either
make `dump --header <dir>` also populate `public_header_dirs` to match
`compare`, or stop `compare` from implicitly deriving public-header
provenance from `--header` at all (requiring an explicit
`--public-header-dir` the way `dump` does) — needs a real product decision
plus its own ADR-050 follow-up, not a drive-by fix.

## What this validates about ADR-053 specifically

None of the five completed pilots above exercised a build system wired
into the new `attribution_path`/`"inferred"` build-output projection
end-to-end (that wiring is explicitly deferred — ADR-053 D5, "CLI/Action
pipeline wiring" — the algorithm, Make link-unit capture, ingest filtering,
and `build_output.py` validator are unit-tested directly, not through a
pilot's CLI surface, since no CLI/Action entry point produces or consumes
`attribution_path` yet). The pilots above validate the surrounding G30
pipeline (config validation, build-output validation, run-plan generation,
baseline resolution, real-project scan/compare correctness across four
build-system shapes and a cross-compiled target) that ADR-053's future
CLI/Action wiring will sit on top of.
