# oneAPI `scan` run — binary-tier findings & accuracy (2026-06)

Real `abicheck` **0.4.0** run (post-PR #440, fresh `main` `e31f357`) against four
Intel oneAPI / UXL libraries on a **clang-only host (no castxml)**. This records
the binary-evidence tier (`--source-method s0` → L0/L1) end-to-end across seven
version pairs, plus the feasibility limits hit on the deeper L2–L5 tiers.

Raw per-pair data: `data/oneapi_scan_2026-06.json`. Reproduce with
`validation/scripts/run_oneapi_scan.py`.

## Environment

- abicheck 0.4.0 (clang 18.1.3; **no castxml**, no abidiff/ABICC); 4 cores / 15 GB.
- Binaries fetched from **conda-forge** (`tbb`, `onednn`, `dal`) and the **Intel
  channel** (`oneccl-devel`, via the new channel-aware harness from PR #440).
- oneDNN threading variant held constant (`tbb_*` build) to avoid a confounder.

## Results (s0, binary L0/L1)

| Lib | Pair | SONAME | Expected¹ | abicheck | Breaking findings² | Wall |
|-----|------|--------|-----------|----------|-------------------|------|
| oneTBB | 2021.12.0→2021.13.0 | `libtbb.so.12` (stable) | COMPATIBLE | **COMPATIBLE_WITH_RISK** | 0 | 0.5 s |
| oneTBB | 2021.13.0→2023.0.0 | `libtbb.so.12` (stable) | ~compatible | **COMPATIBLE_WITH_RISK** | **0** | 0.5 s |
| oneDNN | 3.11→3.12 | `libdnnl.so.3` (stable) | COMPATIBLE | **BREAKING** | 6 | 4.2 s |
| oneDNN | 2.7.2→3.0 | `.so.2`→`.so.3` (bump) | BREAKING | **BREAKING** | 92 | 2.7 s |
| oneDAL | 2025.0.0→2025.1.0 | `libonedal_core.so.3` (stable) | COMPATIBLE | **BREAKING** | 164 | 52 s |
| oneDAL | 2024.7.0→2025.0.0 | `.so.2`→`.so.3` (bump) | BREAKING | **BREAKING** | 20524 | 61 s |
| oneCCL | 2021.12.0→2021.13.0 | `libccl.so.1` (stable) | COMPATIBLE | **COMPATIBLE_WITH_RISK** | 0 | 15 s |

¹ "Expected" = SONAME/SemVer convention from a *public-header* viewpoint (no
independent ABICC oracle tracks these four). Treat this as a **case study**, not a
statistical accuracy figure — n is tiny and the labels are convention-derived.

² "Breaking findings" = abicheck's **breaking-classified** count (the scan's
`diff.breaking`, recorded per pair in `data/oneapi_scan_2026-06.json`). This is
*not* the same as a raw count of removed exported symbols — abicheck groups and
classifies, so e.g. oneDAL 2025.0→2025.1 yields **164** breaking findings against
**212** raw removed exported `FUNC`/`OBJECT` symbols seen via `readelf` (§ below).

## Accuracy analysis

**No usable DWARF on any binary** — `has_dwarf` (a rigorous `.debug_info` +
`DW_TAG_subprogram` check) is **false on both sides of all seven pairs**
(`dwarf_old` *and* `dwarf_new` in the dataset). The `dwarf_*` probe is the
authoritative signal here — **not** abicheck's own `L1_debug` coverage row, which
reads `present`. That row is a coverage artifact, not proof of a debug section:
`cli_scan` marks L1 `present` whenever `snap.dwarf is not None`, and `dumper`
attaches an *empty* `DwarfMetadata` even in the symbol-only fallback. So the
libraries are effectively **symbols-only** for type purposes. Combined with the
**L2 header tier not being reached** (see
limitations), *every* verdict here is **binary-strict**: it treats every exported
symbol as ABI. That lens is correct but stricter than a
public-header oracle, and the divergences are all explained by it:

- **SONAME-bump pairs → BREAKING, correct (true positives).** oneDNN `.so.2→.so.3`
  and oneDAL `.so.2→.so.3` are genuine major breaks. ✓
- **oneTBB (both pairs) → 0 breaking findings, correct.** Despite real removals of
  internal `tbb::detail::r1::*` symbols across 2021.13→2023.0.0 (confirmed via
  `nm`), abicheck's default scoping **correctly demotes** them — no false positive.
  This is the headline good result. ✓
- **oneCCL minor → COMPATIBLE_WITH_RISK, correct.** ✓
- **oneDNN 3.11→3.12 → BREAKING is a *scope divergence*, not a clear FP.** The 6
  breaking findings are all `func_removed_elf_only`: one experimental free function
  (`sdpa_primitive_desc_create`) + five **internal** `dnnl::impl::graph::*`
  `std::call_once` guard symbols. None are public `dnnl_*` C API or public `dnnl::`
  C++ classes. A header-scoped view would call this COMPATIBLE; binary-strict
  correctly reports real removals of exported-but-internal symbols.
- **oneDAL 2025.0→2025.1 → BREAKING is also a scope divergence.** abicheck reports
  **164 breaking findings**; independently, `readelf` shows **212 raw removed
  exported symbols** (the two are different metrics — see footnote ²). The bulk of
  the removed symbols are **bundled BLAS/LAPACK routines** (`DGETRF`, `SGETRF`,
  `DGETRS`, …) — 0 of the first 300 are in `daal::`. This is a *packaging* change
  in what `libonedal_core` re-exports (MKL/LAPACK bundling), not a change to
  oneDAL's own public API. Real binary-ABI change; not a oneDAL
  API break.

**Bucketed:** 3 correct non-breaking, 2 correct breaking (SONAME bumps), 2
binary-strict scope divergences (oneDNN/oneDAL minor) that a public-header scope
would demote. **Zero clear false positives** — every breaking verdict corresponds
to symbols genuinely removed from the binary; the question is only public-ness,
which needs the L2 header tier to settle.

## Timing

- Cost scales with exported-symbol count, as expected: oneTBB (~1 k syms) 0.5 s;
  oneDNN (~mid) 3–4 s; **oneCCL 15 s; oneDAL 52–61 s** (125–149 MB, ~10–25 k
  symbols). All s0; no L4 cliff is paid at this tier.

## Limitations hit (deeper tiers)

- **L2 (clang header AST) needs the full TU flag set.** Scanning `oneapi/tbb.h`
  via the clang L2 backend failed in a cascade — each fix surfaced the next:
  (1) nested `oneapi/...` includes → needs `-I <include-root>`; (2) `<cstddef>`
  not found → **clang doesn't auto-detect the GCC libstdc++ toolchain** here
  (headers at `/usr/include/c++/13`); (3) a `-std=c++NN` is then required. The bare
  `scan -H` path supplies none of these, confirming the documented "L2 needs the
  TU's compile context" limit on a clang-only host. **Consequence:** the
  public/internal boundary (which would demote the oneDNN/oneDAL scope divergences
  to COMPATIBLE) could not be established here.
- **L3–L5 (s1/s2/s4/s5/s6) need a compile DB.** Not exercised in this run; conda
  ships no `compile_commands.json`, so these require a configure step or
  `build.query` (oneDAL would use the Bazel `aquery` adapter).

## Conclusions

- The PR #440 scanner runs cleanly on real oneAPI binaries; the Intel-channel
  harness fetches oneCCL correctly.
- Binary-strict `s0` gives the **right verdict on SONAME bumps** and **correctly
  demotes oneTBB's internal-symbol churn** (no FP). The oneDNN/oneDAL minor-bump
  BREAKINGs are **real exported-symbol removals scoped outside each library's
  public API** — the documented scope-divergence case, resolvable only with the
  L2 header tier (blocked here by the clang-toolchain setup, not by abicheck).
- Next step to close the loop: establish L2 by passing the resolved clang flags
  (`-I` include roots + libstdc++ paths + `-std`), then re-run the two divergent
  pairs under public-header scoping to confirm they demote to COMPATIBLE.
