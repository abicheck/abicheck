# UXL real-scan run — source-scan levels, timing & usability (2026-06)

**Date:** 2026-06-15
**abicheck version:** 0.3.0 (`napetrov/abicheck`, branch `claude/uxl-scans-timing-o2sthi`)
**Author:** automated scan run (Claude Code session)
**Toolchain on the box:** gcc/g++ 13.3, clang 18, cmake 3.28, ninja — **no castxml**, 4 vCPU / 15 GiB.

**Purpose.** Run the *full stack of checks* (`abicheck scan`, the ADR-035 D3
orchestrator) against real UXL Foundation projects, sweeping every
**source-scan level** (`--source-method s0…s6` and the `--mode` presets), and
record (a) wall-clock **timing per level** and (b) **usability** observations —
what works out of the box, what silently degrades, and where the cost lives.

All binaries are built from upstream release tags in this environment (not
synthetic fixtures); reproduce them from the commands in §6. Raw machine results
are committed under `validation/data/uxl_scan_results_*.json`.

---

## 1. Subjects

| Project | Kind | Old → New | SONAME | Exported funcs | .so size | Compile units |
|---|---|---|---|---|---|---|
| **oneTBB** | C++ (template/STL-heavy) | v2021.12.0 → v2021.13.0 | `libtbb.so.12` (unchanged) | 386 → 388 | 4.66 → 4.68 MB | 40 |
| **UMF** (Unified Memory Framework) | C (clean `umf_*` surface) | v0.10.0 → v0.11.0 | `libumf.so.0` (unchanged) | 316 → 382 | 0.42 → 0.54 MB | 50 |

These bracket the two failure modes that matter: oneTBB is a same-SONAME
*minor* bump (the "is this safe to ship?" case, dominated by C++ internal-namespace
noise), and UMF is a pre-1.0 library that genuinely churns its ABI without
bumping SONAME (the "real break hiding behind a stable SONAME" case).

Both were built `RelWithDebInfo` with `CMAKE_EXPORT_COMPILE_COMMANDS=ON` so the
full L3 (compile-DB) → L4 (source replay) → L5 (source graph) stack is reachable
via `--sources`/`--compile-db`. Public headers were **not** fed (see U1) because
L2 header parsing is castxml-only and castxml is not installed.

---

## 2. Source-scan levels, in one picture

`scan` resolves two orthogonal axes (`abicheck/buildsource/scan_levels.py`):
the **S-method** (`s0…s6`, *how* source evidence is gathered) and the **L-depth**
(`headers|build|source|full|graph`, *what* it reaches). `--mode` is a fixed
(S, L) preset. The cost structure falls cleanly into two tiers:

| Tier | Levels | What runs | L3 | L4 | L5 |
|---|---|---|---|---|---|
| **Cheap (no AST)** | s0, s1, s3, s4 | binary+DWARF diff, lexical pattern scan, compile-DB read, symbol/graph index | s1/s4 | — | s4 |
| **Expensive (AST replay)** | s5, s6, and modes `pr`/`pr-deep`/`baseline`/`audit` | clang per-TU AST replay → source-ABI surface | ✓ | partial | ✓ |

---

## 3. Timing & verdict per level

### 3.1 oneTBB (v2021.12.0 → v2021.13.0)

| Level | Resolved | exit | **wall-clock** | verdict | diff (brk/api/risk/compat) | L3 | L4 | L5 |
|---|---|---|---:|---|---|---|---|---|
| `--source-method s0` | s0 / headers | 4 | **29.0 s** | BREAKING | 2 / 0 / 0 / 3 | off | off | off |
| `--source-method s1` | s1 / build | 4 | **28.9 s** | BREAKING | 2 / 0 / 0 / 3 | ✓ | — | — |
| `--source-method s3` | s3 / headers | 4 | **29.1 s** | BREAKING | 2 / 0 / 0 / 3 | — | — | — |
| `--source-method s4` | s4 / graph | 4 | **29.2 s** | BREAKING | 2 / 0 / 0 / 3 | ✓ | — | ✓ |
| `--source-method s5` | s5 / source | 4 | **222.2 s** | BREAKING | 2 / 0 / 0 / 3 | ✓ | partial | ✓ |
| `--source-method s6` | s6 / full | 4 | **215.1 s** | BREAKING | 2 / 0 / 0 / 3 | ✓ | partial | ✓ |
| `--mode pr` | s5 / source | 4 | **211.3 s** | BREAKING | 2 / 0 / 0 / 3 | ✓ | partial | ✓ |
| `--mode pr-deep` | s5 / graph | 4 | **216.1 s** | BREAKING | 2 / 0 / 0 / 3 | ✓ | partial | ✓ |
| `--mode baseline` | s6 / full | 4 | **211.8 s** | BREAKING | 2 / 0 / 0 / 3 | ✓ | partial | ✓ |
| `--mode audit` (no baseline) | s5 / source | 0 | **211.8 s** | COMPATIBLE | intra-version | ✓ | partial | ✓ |

### 3.2 UMF (v0.10.0 → v0.11.0)

| Level | Resolved | exit | **wall-clock** | verdict | diff (brk/api/risk/compat) | L3 | L4 | L5 |
|---|---|---|---:|---|---|---|---|---|
| `--source-method s0` | s0 / headers | 4 | **16.9 s** | BREAKING | 17 / 0 / 104 / 32 | off | off | off |
| `--source-method s1` | s1 / build | 4 | **16.9 s** | BREAKING | 17 / 0 / 104 / 32 | ✓ | — | — |
| `--source-method s3` | s3 / headers | 4 | **16.9 s** | BREAKING | 17 / 0 / 104 / 32 | — | — | — |
| `--source-method s4` | s4 / graph | 4 | **17.0 s** | BREAKING | 17 / 0 / 104 / 32 | ✓ | — | ✓ |
| `--source-method s5` | s5 / source | 4 | **21.6 s** | BREAKING | 17 / 0 / 104 / 32 | ✓ | partial | ✓ |
| `--source-method s6` | s6 / full | 4 | **21.4 s** | BREAKING | 17 / 0 / 104 / 32 | ✓ | partial | ✓ |
| `--mode audit` (no baseline) | s5 / source | 0 | **21.8 s** | COMPATIBLE | intra-version | ✓ | partial | ✓ |

### 3.3 The POI-scoping control (oneTBB, s5 + a one-file diff seed)

| Run | scope | **wall-clock** | verdict | diff |
|---|---|---:|---|---|
| `s5` no seed (from §3.1) | broad (all 40 TUs) | **222.2 s** | BREAKING | 2 / 0 / 0 / 3 |
| `s5 --changed-path src/tbb/version.cpp` | 1 TU | **11.5 s** | BREAKING | 2 / 0 / 0 / 3 |

Same verdict, same coverage (L3 present / L4 partial / L5 present), **~19× faster.**

---

## 4. What the timing says

1. **There is one cost cliff, at the L4 AST boundary (s4 → s5), and its size
   depends on the *source language*, not the binary.** On oneTBB (C++,
   STL/template-heavy) the cheap levels s0–s4 land at **~29 s** and the AST
   levels s5/s6 jump to **~210–222 s** — a **~7× cliff**. On UMF (C) the same
   boundary moves from **~17 s** to only **~21 s** — a **~1.3× bump**. The L4
   cost is clang per-TU AST replay, and it scales with C++ template/STL
   instantiation depth; a plain-C surface barely feels it. **The expensive
   tier is only expensive for heavy C++.**

2. **The cheap tier is dominated by the binary dump + always-on pattern scan,
   not by the source layer.** s0 (pure diff classifier, no L output) costs the
   same as s1/s3/s4 on each project (~29 s oneTBB, ~17 s UMF) — the floor is
   "parse the DWARF `.so` + lexical-scan the tree", and it tracks `.so`/source
   size (4.6 MB / 632 files vs 0.5 MB).

3. **s4 is the structure sweet spot.** s4 builds the L5 reachability graph
   *without* L4 replay (`graph-build` collect-mode) and stays in the cheap tier
   on both projects. If you want impact/reachability structure but not
   source-body semantics, s4 is ~7× cheaper than s5 on oneTBB for the same L5.

4. **"Targeted" s5 is only cheap *with* a diff seed.** Unseeded, s5/`pr`
   (`source-changed`) and s6/`baseline` (`graph-full`) are within noise
   (~211–222 s): with no `--since`/`--changed-path` the changed set is empty and
   POI scoping has nothing to narrow to, so it replays every TU. **Hand it a
   one-file seed and the same s5 drops from 222 s to 11.5 s (~19×, identical
   verdict)** — §3.3. The PR-tier promise is real but seed-gated (U3).

5. **`pr-deep` ≈ `pr`, and `audit` ≈ the baseline modes, in cost.** The extra
   L5 graph edges in `pr-deep` are cheap on top of the L4 replay; and `audit`,
   despite running *no baseline comparison*, costs the same ~212 s (oneTBB) /
   ~22 s (UMF) — confirming the wall-clock is the **collection** of the new
   side's L4/L5 evidence, not the diff.

---

## 5. What the verdicts say (quality)

**The source-scan depth did not change the verdict on either project.** oneTBB is
`BREAKING` at every level; UMF is `BREAKING` at every level. The authoritative
L0/L1 binary+DWARF diff already decides the gate; s1–s6 add **coverage,
localization and explanation**, not a different outcome. This is exactly the
ADR-028 D3 authority rule holding in the field: L3–L5 never manufacture or erase
a binary-proven verdict.

### 5.1 oneTBB — surface scoping now works (a real improvement)

The prior validation report (`validation/REPORT.md`) recorded a ~90 %
false-positive avalanche on oneTBB in DWARF-only mode. **That avalanche is
gone.** This run produces only **2 breaking + 3 compatible** findings, and the
surface-scoping is visibly doing its job:

- ✅ `tbb::detail::r1::cpu_features_type` size change (16→24 bits) was
  **filtered as `private-internal-unreachable`** — correctly suppressed.
- ⚠️ `tbb::detail::r1::thread_request_serializer` (`my_total_request` `int → atomic<int>`)
  was **kept** and drives an `internal_type_leaks_via_public_api` finding —
  abicheck traced a reachability path from the public surface into the internal
  type.

That second finding is **defensible but arguably over-aggressive**: the
reported reachability path runs through a `std::unique_ptr` member
(`_M_t → …_proxy → my_serializer`), i.e. *pointer* indirection. A field-type
change behind a pointer does **not** change the holder's size/offset, yet the
finding's rationale text says "embedded-by-value or via inheritance — layout
change propagates to public type size/offset." Worth a closer look (§7 Q1):
either the path-classifier should distinguish by-value embedding from
behind-pointer reachability, or the rationale text overstates the impact.

The `soname_bump_recommended` advisory (2 incompatible changes, SONAME still
`libtbb.so.12`) is correct and useful.

### 5.2 UMF — a real, correctly-detected ABI break

UMF is pre-1.0 and genuinely churned its ABI from v0.10 to v0.11 without
bumping SONAME (`libumf.so.0` on both sides). abicheck caught it cleanly —
**17 breaking, 104 risk, 32 compatible**, verdict `BREAKING` — and on a C
surface there is no internal-namespace noise to filter:

- **17 breaking**, all on documented public `umf_*` types: 9
  `type_field_offset_changed` and 2 `type_size_changed` on
  `umf_memory_provider_ops_t` / `umf_memory_pool_t` / `umf_memory_provider_ext_ops_t`
  (real struct-layout breaks), 2 `func_removed`
  (`umfCoarseMemoryProviderGetStats` & friends), plus `type_removed`,
  `typedef_removed`, `type_field_removed`, and a removed `UMF_1.0`
  `symbol_version_node`.
- **104 risk** are all `symbol_moved_version_node` — UMF re-versioned its
  exported symbols between releases. Correctly classified as RISK
  (deployment/linkage concern), not BREAKING.

No false positives observed. This is the clean-C counterpart to oneTBB's
C++-internal-namespace case, and abicheck handles both correctly.

---

## 6. Reproduce

```bash
# oneTBB
git clone --depth 1 -b v2021.12.0 https://github.com/uxlfoundation/oneTBB tbb-old
git clone --depth 1 -b v2021.13.0 https://github.com/uxlfoundation/oneTBB tbb-new
for s in old new; do
  cmake -S tbb-$s -B build-$s -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DTBB_TEST=OFF -DTBB_STRICT=OFF
  ninja -C build-$s tbb
done
abicheck dump build-old/gnu_*/libtbb.so.12.12 -o old.abi.json
abicheck scan --binary build-new/gnu_*/libtbb.so.12.13 \
              --sources tbb-new --compile-db build-new/compile_commands.json \
              --baseline old.abi.json --source-method s5   # …or s0/s1/s3/s4/s6, --mode …

# UMF
git clone --depth 1 -b v0.10.0 https://github.com/oneapi-src/unified-memory-framework umf-old
git clone --depth 1 -b v0.11.0 https://github.com/oneapi-src/unified-memory-framework umf-new
for s in old new; do
  cmake -S umf-$s -B umf-build-$s -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo \
        -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DUMF_BUILD_TESTS=OFF -DUMF_BUILD_EXAMPLES=OFF \
        -DUMF_BUILD_SHARED_LIBRARY=ON -DUMF_DISABLE_HWLOC=ON \
        -DUMF_BUILD_LEVEL_ZERO_PROVIDER=OFF -DUMF_BUILD_CUDA_PROVIDER=OFF
  ninja -C umf-build-$s umf
done
```

---

## 7. Usability findings

| # | Severity | Finding |
|---|---|---|
| **U1** | **High** | **L2 header parsing is castxml-only; clang is not used for headers.** `scan -H <dir>` and `dump --headers` fail hard with `castxml not found in PATH` even though clang 18 is installed and the L4 replay path *does* use clang. On a clang-only box (the common case) you cannot get header-aware public-surface scoping at all. |
| **U2** | **High** | **The entire cross-source check layer (D4) silently skips without public headers.** All four crosschecks (`exported_not_public`, `public_not_exported`, `header_build_context_mismatch`, `private_header_leak`) report `skipped — no public-header provenance`. Because of U1, on a clang-only box this whole feature class is unreachable by default. The skip is honest (not a false "pass"), but the net effect is a major advertised capability being off. |
| **U3** | Medium | **"Targeted" PR scoping is a no-op without a diff seed — and the default leaves it off.** Running `--mode pr` / `s5` without `--since` or `--changed-path` yields `changed paths: 0 (broad scope)` and replays every TU — same ~222 s as `s6`. Add a one-file `--changed-path` and the *same* s5 drops to **11.5 s** for the identical verdict (§3.3). The cheap-PR speedup is real but entirely seed-gated; absent a seed the user silently pays full `s6` price under a `pr` label. Consider defaulting `--since origin/HEAD` inside a git repo, or warning when a `pr`-family mode runs with an empty changed set. |
| **U4** | Low | **The cliff size is a C++ phenomenon — set expectations accordingly.** s4→s5 is ~7× on oneTBB (C++) but only ~1.3× on UMF (C). Docs/`--budget` guidance that assumes "L4 is always minutes" will over-budget C projects and a single seedless C++ scan can blow a tight CI budget. The honest knob is the diff seed (U3), not the level. |
| **U5** | Low | **`L4_source_abi` coverage row reports `partial` with an empty detail string** — no TU count or hit/miss numbers, unlike the L3 row ("40 compile units"). Hard to tell *how* partial. |
| **U6** | Low | **`dump` with no headers emits a `UserWarning` to stderr** on every run ("No headers provided — using DWARF…"). Expected, but it's noise on the common stripped/no-header path and looks like a defect in logs. |
| **U7** | Info | **Verdict is stable across source depth — so for a gate, run cheap.** Since s0–s6 produced identical verdicts here, a CI *gate* gains nothing from the AST tiers on these inputs; the AST cost buys richer evidence/localization for humans, not a different pass/fail. Worth surfacing in docs as "pick your level by *why you're scanning*, not by hoping for a different verdict." |

### Open questions for maintainers

- **Q1 (quality).** Should `internal_type_leaks_via_public_api` treat
  reachability-through-`std::unique_ptr`/pointer differently from by-value
  embedding? The oneTBB `thread_request_serializer` finding traverses a
  `unique_ptr` yet is described as a size/offset-propagating layout break (§5.1).
- **Q2 (usability).** Can L2 fall back to clang (the `--source-abi-extractor clang`
  backend already exists for L4) so header-aware scoping + the D4 crosschecks
  work on a clang-only host (U1/U2)?

---

## 8. Bottom line

- **Timing:** one cliff, at the L4 AST boundary, and its height is a C++
  phenomenon. s0–s4 ≈ 29 s (oneTBB) / 17 s (UMF), dominated by the DWARF dump +
  pattern scan; s5/s6 ≈ 3.5 min on C++-heavy oneTBB but only ~21 s on C UMF.
  The `--mode` presets behave as documented, but the PR-tier speedup is
  seed-gated — a one-file seed took oneTBB s5 from 222 s to 11.5 s.
- **Quality:** the old DWARF-only false-positive avalanche on oneTBB is fixed —
  surface scoping correctly filters truly-internal types and keeps genuinely
  reachable ones; UMF's real break is caught cleanly. One reachability-rationale
  edge (Q1) is worth a look.
- **Usability:** the biggest gap is castxml-gated L2 (U1) cascading into the
  crosscheck layer being unavailable on a clang-only box (U2).
