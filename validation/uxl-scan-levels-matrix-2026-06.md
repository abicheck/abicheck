# UXL real-product scan-level matrix (2026-06)

Real `abicheck scan`/`compare` runs across source-scan levels (`s0`–`s6`) on
**four** UXL Foundation C/C++ libraries, on a clang-only host (no castxml),
15.1 GiB RAM, `ABICHECK_L4_JOBS=8`. This extends the earlier two-library run
([`uxl-scan-levels-timing-2026-06.md`](uxl-scan-levels-timing-2026-06.md)) with
oneDNN (cheap tier) and oneDAL (binary tier). Raw per-run data:
[`data/uxl_scan_levels_consolidated_2026-06.json`](data/uxl_scan_levels_consolidated_2026-06.json).

Binaries are pinned conda-forge packages; sources are the matching GitHub
release tarballs (not committed — reproduce from the versions below).

## Subjects

| Product | lang | soname | old → new | tier |
|---------|------|--------|-----------|------|
| oneTBB | C++ | `libtbb.so.12` | v2021.12.0 → v2021.13.0 | `s0,s1,s3,s4,s5,s6` |
| UMF | C | `libumf.so.0` | v0.10.0 → v0.11.0 | `s0,s1,s3,s4,s5,s6` |
| oneDNN | C++ | `libdnnl.so.3` | v3.11.3 → v3.12 | `s0,s1,s3,s4` |
| oneDAL | C++ | `libonedal_core.so.4` | 2026.0.0 → 2026.1.0 | `s0` |

`s2` (the preprocessor source method) was **not** measured in this matrix — the
levels run are exactly those listed above. All times were captured with
`--sources` supplied at every level (the harness always passes it), so even the
`s0` column carries the source-path setup cost; the pure binary-only `s0` (no
`--sources`) is cheaper — for oneDNN, **5.3 s vs the 38.6 s** shown below.

## Verdict × level × time

`CWR` = COMPATIBLE_WITH_RISK, `BRK` = BREAKING. Times are wall-clock seconds.

| Product | s0 | s1 | s3 | s4 | s5 | s6 |
|---------|----|----|----|----|----|----|
| oneTBB | CWR 5.5 | CWR 28.8 | CWR 5.6 | CWR 28.5 | **OOM** 339.6 | **OOM** 503.1 |
| UMF | BRK 17.9 | BRK 20.5 | BRK 18.2 | BRK 20.4 | BRK 38.7 | BRK 39.1 |
| oneDNN | BRK 38.6 | BRK 135.3 | BRK 44.0 | BRK 141.7 | *omitted (RAM ceiling)* | — |
| oneDAL | BRK 8.8 | — | — | — | — | — |

**Layer activation** (where collected):

- **Zero-config L3** (cmake-inferred compile DB, *no build step, no flags*) is
  `present` at `s1`/`s4` for **oneTBB and oneDNN** — the #456 headline path now
  exercised on two independent real C++ trees. S2 preprocessor scan is `partial`
  there.
- **UMF** runs L3 + S2 `present` at `s1`/`s4` (operator pre-configure feeds the
  compile DB via `--build-info`, since UMF's CMake network-fetches hwloc/level-zero
  which the sandbox proxy blocks) and full **L4 + L5** at `s5`/`s6`.
- **oneDNN** `s4` adds **L5** (`present`); its `s5`/`s6` full-target L4 is omitted
  (see RAM ceiling below).

## What broke (real findings)

From `compare` on the new-vs-old binaries:

| Product | verdict | total | top change kinds |
|---------|---------|-------|------------------|
| oneTBB `libtbb.so.12` | COMPATIBLE_WITH_RISK | — | surface-scoped; the old ~90 % DWARF-only false-positive flood stays suppressed |
| UMF `libumf.so.0` | BREAKING | 131 | 104 `symbol_moved_version_node`, 21 `func_added`, 2 `func_removed_elf_only`, 1 `soname_bump_recommended` |
| oneDNN `libdnnl.so.3` | BREAKING | 19 | 16 `func_added`, 1 `func_removed_elf_only`, 1 `visibility_leak`, 1 `soname_bump_recommended` |
| oneDAL `libonedal_core.so.4` | BREAKING | 2138 | **2134 `func_removed_elf_only`**, 2 `symbol_leaked_from_dependency_changed`, 1 `visibility_leak`, 1 `soname_bump_recommended` |

**Headline:** oneDAL's *minor* release (2026.0.0 → 2026.1.0) **removed 2134
exported functions on a same-major soname** (`libonedal_core.so.4`). abicheck
flags it BREAKING and recommends a soname bump — exactly the class of silent
break a version-string check misses.

## Conclusions

1. **Verdict is depth-invariant.** Every product reaches its final verdict at
   `s0` (L0/L1 artifact evidence); `s1`–`s6` only add localization/context
   (L3 build flags, S2 macros, L4 source ABI, L5 graph). The gate is the binary.
2. **One cost cliff, at L4 (`s4`→`s5`), height ∝ C++ template depth.** UMF (C)
   barely moves (~20 s → ~39 s); the template-heavy C++ trees are where L4
   explodes.
3. **That L4 tier is RAM-bound on a 15 GiB host for large C++ products.** oneTBB
   `s5`/`s6` were **OOM-killed** (`exit -9`) replaying 181 zero-config TUs at
   8 workers; oneDNN (larger) `s5`/`s6` were omitted for the same reason. The
   companion fix (PR #458) makes the L4 worker count RAM- and cgroup-aware so this
   degrades gracefully (fewer workers) instead of crashing; the durable
   recommendation for a template-heavy tree on a constrained host is a
   **seeded/scoped** scan (`--since`/`--changed-path`) rather than a full-target
   `s5`/`s6`.
4. **Zero-config L3 generalizes.** Proven now on oneTBB *and* oneDNN — `--sources`
   alone yields L3 build context with no build and no flags.

## Reproduce

Conda binaries: `https://conda.anaconda.org/conda-forge/linux-64/<file>`
(`tbb-2021.12.0-h84d6215_4.conda` / `tbb-2021.13.0-hb700be7_6.conda`;
`umf-0.10.0-hb3528f5_356.conda` / `umf-0.11.0-h8a59203_396.conda`;
`onednn-3.11.3-dpcpp_he0a1cb6_0.conda` / `onednn-3.12-dpcpp_he0a1cb6_0.conda`;
`dal-2026.0.0-h74c4b1a_1017.conda` / `dal-2026.1.0-h74c4b1a_14.conda`). Sources:
`https://github.com/uxlfoundation/{oneTBB,unified-memory-framework,oneDNN,oneDAL}/archive/refs/tags/<tag>.tar.gz`.
