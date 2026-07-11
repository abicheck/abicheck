# PVXS ABI-scan validation (2026-07)

Real-world validation of abicheck against **[epics-base/pvxs](https://github.com/epics-base/pvxs)**
— a C++ PVAccess client/server library that ships two shared objects
(`libpvxs`, `libpvxsIoc`). PVXS already tracks ABI: its `abi-diff.sh` drives
`abi-dumper` + `abi-compliance-checker` (ACC) with `-public-headers` scoping,
and the maintainer notes *"I don't totally trust abicc, so let's have a second
opinion"* (an `nm -g` symbol diff). abicheck is a drop-in ACC replacement, so
pvxs is a representative integration target.

This run built four releases from source, scanned them across evidence levels
L0–L5, and drove the goal of the upcoming release: **full pvxs scans with no
usability / performance / accuracy blockers.** Three code defects were found and
fixed; two usage requirements and two minor gaps are documented below.

## Reproduction

Toolchain on the runner: gcc/g++ 13.3, clang 18.1 (**no castxml, no abidiff/ACC**),
Python 3.11, 4 cores.

```sh
# EPICS Base (dependency), then pvxs per tag with debug info + public headers
git clone --branch R7.0.8.1 https://github.com/epics-base/epics-base && make -C epics-base -j4
for tag in 1.4.0 1.5.0 1.5.1 1.5.2; do
  git -C pvxs archive "$tag" | tar -C build/$tag -x
  echo "EPICS_BASE=$PWD/epics-base" > build/$tag/configure/RELEASE.local
  make -C build/$tag CROSS_COMPILER_TARGET_ARCHS= OPT_CFLAGS='-g -Og' OPT_CXXFLAGS='-g -Og' ioc -j4
done
```

Each build yields `libpvxs.so.<abi>` (~8.5 MB, DWARF) and `libpvxsIoc.so.<abi>`
(~3.1 MB). SONAME bumped `libpvxs.so.1.4` → `.1.5` between 1.4.0 and 1.5.0.

## Verdict matrix (L1, DWARF-only, after fixes)

| Pair | libpvxs | libpvxsIoc | Notes |
|------|---------|------------|-------|
| 1.4.0 → 1.5.0 | **BREAKING** (104) | **BREAKING** (25) | Real: SONAME bump, minor release |
| 1.5.0 → 1.5.1 | COMPATIBLE_WITH_RISK (4) | COMPATIBLE (1) | Patch |
| 1.5.1 → 1.5.2 | **BREAKING** (28) | **BREAKING** (41) | Patch — over-called at L1; see F3 |

The 1.5.1 → 1.5.2 `BREAKING` at L1 is driven by a **single** change: a field
added to `pvxs::client::OperationBase`. That type lives in `src/clientimpl.h`
— an **internal, non-installed** header — and the field addition is real, but
the type is not part of the public ABI. Rescoping to the public headers demotes
it correctly (F3).

| 1.5.1 → 1.5.2 libpvxs | Verdict | Findings |
|------|---------|----------|
| L1 DWARF-only, no scoping | **BREAKING** | 1 breaking (internal `OperationBase`) + 27 risk |
| L2 headers + `--scope-public-headers` | **COMPATIBLE_WITH_RISK** | 5 risk (internal-leak churn + RUNPATH) |

L2 header parsing used the **clang** JSON-AST frontend (`--ast-frontend clang`),
confirming full header-scoped scans work on a castxml-less host.

## Source scan with build integration (L3/L4/L5)

`scan --depth source` over `libpvxs 1.5.2` with a `compile_commands.json`
(generated from the EPICS Make build) wired in:

```
abicheck scan --binary libpvxs.so.1.5 -H include \
  -I include -I <epics-base>/include -I <epics-base>/include/os/Linux -I <epics-base>/include/compiler/gcc \
  --sources . --compile-db compile_commands.json --depth source \
  --public-header-dir include --ast-frontend clang
```

All layers populated in **129 s** (candidate snapshot 109 s, pattern scan 14 s):
L0 (1104 exports) · L1 DWARF · L2 (632 public-header types) · L3 (61 compile
units) · L4 source-ABI replay · L5 source graph. Cross-source hygiene surfaced
**470 findings** — notably `odr_type_variant` ×67 (divergent cross-TU layouts),
`header_build_context_mismatch`, and `public_to_internal_dependency` ×2 — worth
maintainer review but advisory (single-build audit; a release gate adds
`--baseline <prev.abi.json>`). The scan correctly advised passing
`--since <ref>` to scope L4/L5 to changed TUs for fast PR runs.

## Findings

### F1 — Performance: O(N²) internal-leak resolution *(fixed)*

A DWARF-only `libpvxs` compare **hung > 340 s** at 99 % CPU. Profiling pinned it
to `internal_leak._resolve_type_name`, which linearly scanned the entire type
map (~4046 types) on every BFS node to canonicalize unqualified names —
quadratic on a large C++ surface.

**Fix** (`abicheck/internal_leak.py`): precompute a final-`::`-segment suffix
index once per walk (mirroring the existing `by_short` index in `idioms.py`),
turning the per-node lookup from O(N) to O(1). Semantics are byte-for-byte
identical (unique match → qualify, ambiguous → keep literal).

| | before | after |
|---|--------|-------|
| libpvxs compare | > 340 s (unbounded) | 64–83 s (now DWARF-parse-bound) |
| libpvxsIoc compare | (never reached) | ~24 s |

### F2 — Accuracy: RTTI alignment false positives *(fixed)*

`exported_object_alignment_reduced` fired **21×** on a clean 1.5.1 → 1.5.2 patch
— every one a `_ZTS*` (RTTI typeinfo-**name**) symbol, reporting bogus drops
like `2048 → 32 bytes`. `value_alignment` is inferred from `st_value`, so for
RTTI name strings it is a linker-placement artifact of the mangled-name length,
not a declared alignment.

**Fix** (`abicheck/diff_platform_elf_symbols.py`): exclude `_ZTV/_ZTI/_ZTS/_ZTT`
from `_check_object_alignment_reduced`, mirroring the identical exemption the
sibling size detector (`_check_symbol_size_change`) already applies. Cut the
scoped 1.5.1 → 1.5.2 finding count from 26 to 5 with zero real signal lost.

### F3 — Accuracy (usage): scope to public headers *(no code change — required usage)*

Binary/DWARF-only scans over-call changes to types in **internal** headers
(`src/clientimpl.h`), yielding false `BREAKING` on patch releases. This is the
documented L1-over-calls-internal-churn behaviour, and exactly why pvxs's
`abi-dumper` already passes `-public-headers`. **Integration requirement:** pass
`-H include/ --scope-public-headers` (or the Action's `header:` +
`scope-public-headers` inputs) so the surface matches pvxs's installed headers.

### F4 — Usability: castxml-missing error hid the clang escape hatch *(fixed)*

On the clang-only runner, `-H` with default `--ast-frontend auto` failed hard
with *"castxml not found in PATH — install with…"* and never mentioned that
`--ast-frontend clang` works. (The no-silent-fallback default is deliberate:
clang JSON-AST omits record layout.) **Fix** (`abicheck/dumper.py`): the error
now also points to `--ast-frontend clang` / `ABICHECK_AST_FRONTEND=clang` and
states the layout-evidence caveat.

### F5 — Scan flow: L2 header parse ignored the build's include dirs *(fixed)*

The loudest gap: a zero-config `scan --sources . --depth source -H include` (no
`-I`, no committed compile DB) **hard-failed** with
`fatal error: 'epicsTime.h' file not found`. Root cause: in
`cli_scan._build_new_snapshot` the L2 aggregate public-header parse runs
*before* the compile DB is resolved, and only ever searched the user's `-I`
inputs — so although the L4 replay compiled every TU fine with the build's
include dirs, the L2 parse of the public headers (which `#include` EPICS Base)
had none. Providing sources genuinely did *not* "just work."

**Fix**: a best-effort `derive_l2_include_dirs` (`buildsource/inline.py`)
resolves the same compile DB the L4 replay uses — explicit `--build-info`,
auto-discovered `compile_commands.json`, or the inferred build-system query —
and returns the de-duplicated, existing `-I`/`-isystem` dirs. `_build_new_snapshot`
seeds them into the L2 parse **only when the user passed no `-I`** (a pure
fallback; explicit `-I` still wins and any resolution failure returns `[]`, so no
working scan regresses). After the fix the same zero-config command runs clean:

```
INFO: L2 header parse: seeded 13 include dir(s) from the build's compile database (no -I given).
L2_header: present — 632 type(s) from public headers    # was: fatal error
```

### F5b — Timing: parse cost tracks *total* debug info, not the public API *(analysis)*

Even after F1, a two-library compare is ~60–80 s and the single-binary source
scan ~130 s — slow for a library with only ~1104 exports. Profiling an L1-only
compare shows **~100 % of the time is DWARF traversal**: `_process_cu` (15 %),
pyelftools `iter_DIE_children` (13 %) / `_get_cached_DIE` (11 %), `parse_dwarf`
(12 %), `_process_struct` (9 %), `_extract_calling_convention` (7 %). libpvxs is
not "small" in DWARF terms: its 8.5 MB `-g` build carries **4046 types** — mostly
`std::`/template/EPICS-internal — and abicheck parses *every* DIE with **no
public-surface pruning**, so cost scales with the whole debug section rather than
the exported surface. Peak RSS is **~1 GB** (the DIE cache + full type model).
Two optimisation opportunities for a follow-up: (a) prune DWARF DIE processing to
types reachable from exported/public symbols; (b) `_extract_calling_convention`
at 7 % of a compare looks like redundant per-DIE work worth memoizing. These are
larger changes than this PR carries and are logged here as the next perf target.

### F6 — Minor: `DW_TAG_ptr_to_member_type` unhandled *(documented)*

DWARF parsing emits `Unknown DWARF type tag: DW_TAG_ptr_to_member_type` (C++
pointer-to-member). Occurs once in `libpvxs`; the type is skipped (warning
noise, no verdict impact). Low-priority modelling gap.

### F7 — Build hygiene: `runpath_changed` is build-path sensitive *(guidance)*

A `runpath_changed` RISK appears because the two sides were built in different
directories (RUNPATH encodes the build tree). In CI both releases must be built
in an identical layout, or the finding suppressed — otherwise it is noise.

## Why the existing test suite missed the functional bugs

Both fixed defects (F1 perf, F2 RTTI) had **100 % line coverage** — they executed
in tests, but no test *observed* the wrong behaviour. The gaps:

- **F1 (O(N²)) — no scale/complexity test.** Unit and Hypothesis tests build
  tiny snapshots (a handful of types); the quadratic only manifests at thousands.
  Coverage measures *reach*, not *cost*. Closed here by
  `test_suffix_index_built_once_per_walk` (deterministic: asserts the index is
  built once per walk, not per node — the exact regression) plus
  `test_scale_many_types_resolves_and_terminates` (3000-type graph; sub-second
  with the fix, minutes without).
- **F2 (RTTI alignment) — synthetic symbols never used real C++ names.** The
  alignment detector's tests used clean names (`g_table`, `g_pool`); no case fed
  a mangled `_ZTS*`/`_ZTV*` symbol, which only appear in real compiled C++.
  Closed by `test_rtti_symbols_are_exempt`. The deeper lesson: detectors that key
  on symbol-name *shape* need at least one real-C++-binary fixture in the FP
  corpus — synthetic-only cases can't reach them.

Takeaway for the release: the property/FP-gate corpus is strong on *verdict*
correctness but blind to *performance* and to *real-mangled-symbol* shapes. This
run adds regression coverage for both; a standing large-real-C++-binary case
(pvxs is a good candidate) in the `libabigail`/`integration` lane would guard the
class going forward.

## Recommended CI integration for pvxs

Modernizes `abi-diff.sh` (manual, ACC-based) into a gated PR check over **both**
libraries with public-header scoping — the semantics pvxs already relies on:

```yaml
# .github/workflows/abi.yml (for epics-base/pvxs)
name: ABI check
on:
  pull_request:
  push:
    tags: ['*']
permissions:
  contents: read
  security-events: write   # required for upload-sarif (see docs/user-guide/github-action.md)
jobs:
  abi:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }
      # Build EPICS Base + the base ref and the PR/tag ref of pvxs with -g -Og,
      # emitting lib/<arch>/ dirs for each (see abi-diff.sh setupsrc()). Export
      # EPICS_BASE so the include: paths below can reference it.
      - name: Build old + new
        run: ./ci/build-two-refs.sh   # produces old/lib/<arch> and new/lib/<arch>
      - name: Compare both libraries (public-header scoped)
        uses: abicheck/abicheck@main
        with:
          old-library: old/lib/linux-x86_64        # directory → compares libpvxs + libpvxsIoc
          new-library: new/lib/linux-x86_64
          header: new/include                       # installed public headers only
          # pvxs public headers include EPICS Base headers, so the L2 parse needs
          # the Base include dirs too (F5); `include:` maps to -I (both sides).
          include: >-
            ${{ env.EPICS_BASE }}/include
            ${{ env.EPICS_BASE }}/include/os/Linux
            ${{ env.EPICS_BASE }}/include/compiler/gcc
          scope-public-headers: 'true'
          fail-on-removed-library: 'true'
          format: sarif
          output-file: abi.sarif                    # the action's input is `output-file`
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with: { sarif_file: abi.sarif }
```

Notes:
- Directory inputs make `compare` fan out over both SONAME-matched libraries.
- `include:` maps to `-I` for the L2 header parse; because pvxs's public headers
  `#include` EPICS Base, its include dirs must be supplied here (F5) — omitting
  them makes header parsing fail with `epicsTime.h file not found`.
- The Action installs castxml, so `--ast-frontend` is unnecessary there; on a
  clang-only host set `ast-frontend: clang` (F4).
- For a deeper nightly, add a `scan` job with `depth: source` and `sources: .`
  to surface the cross-source hygiene signal (ODR variants, build-context
  drift) from the L3–L5 layers.

## Status

- **Fixed & tested in this branch:** F1 (perf O(N²)), F2 (RTTI alignment FP),
  F4 (castxml-error UX), **F5 (zero-config `--sources` L2 include seeding)**,
  plus scale/RTTI/derive-includes regression tests. Full fast unit suite green;
  mypy + ruff + AI-readiness clean.
- **Documented for the release:** F3 (public-header-scoping requirement),
  F5b (DWARF-parse-dominated timing + ~1 GB memory; next perf target is
  public-surface DIE pruning), F6 (`ptr_to_member` warning), F7 (RUNPATH).
- Binaries are not committed (per `validation/` convention); reproduce with the
  commands above.
