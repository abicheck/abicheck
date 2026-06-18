# oneAPI `scan` run — binary-tier findings & accuracy (2026-06)

Real `abicheck` **0.4.0** run (post-PR #440, fresh `main` `e31f357`) against four
Intel oneAPI / UXL libraries on a **clang-only host (no castxml)**. This records
the binary-evidence tier (`--source-method s0` → L0/L1) end-to-end across seven
version pairs. The **[L2 finalization](#l2-finalization-post-pr-444--scope-divergences-resolved)**
section below (added after PR #444 unblocked the clang L2 backend on GNU hosts)
re-runs the divergent pairs under public-header scope and resolves the
scope-divergence questions raised in the binary-tier analysis.

Raw per-pair data: `data/oneapi_scan_2026-06.json` (binary tier) and
`data/oneapi_scan_l2_2026-06.json` (L2). Reproduce with
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
- **oneDNN 3.11→3.12 → BREAKING is correct, mostly internal noise + one real
  public change.** The 6 breaking findings are all `func_removed_elf_only`: one
  experimental free function (`sdpa_primitive_desc_create`) + five **internal**
  `dnnl::impl::graph::*` `std::call_once` guard symbols — none public `dnnl_*` C
  API or public `dnnl::` C++ classes, so the *removed exports* are internal. But
  the header-scoped rerun does **not** fully demote this row: the
  [L2 finalization](#l2-finalization-post-pr-444--scope-divergences-resolved)
  below also **surfaces one genuine public-API change** the binary tier could not
  name — the `dnnl::memory::format_tag::format_tag_last` enum sentinel shifted
  (3.12 added format tags). So binary-strict BREAKING is the right verdict, and
  L2 refines *why* (one real public change, not pure scope divergence).
- **oneDAL 2025.0→2025.1 → BREAKING is also a scope divergence.** abicheck reports
  **164 breaking findings**; independently, `readelf` shows **212 raw removed
  exported symbols** (the two are different metrics — see footnote ²). The bulk of
  the removed symbols are **bundled BLAS/LAPACK routines** (`DGETRF`, `SGETRF`,
  `DGETRS`, …) — 0 of the first 300 are in `daal::`. This is a *packaging* change
  in what `libonedal_core` re-exports (MKL/LAPACK bundling), not a change to
  oneDAL's own public API. Real binary-ABI change; not a oneDAL
  API break.

**Bucketed:** 3 correct non-breaking, 2 correct breaking (SONAME bumps), 2
binary-strict minor pairs (oneDNN/oneDAL) whose public-ness the binary tier
cannot settle on its own. **Zero clear false positives** — every breaking verdict
corresponds to symbols genuinely removed from the binary. The public-ness is
**settled in the [L2 finalization](#l2-finalization-post-pr-444--scope-divergences-resolved)
below**: oneDAL fully demotes to **NO_CHANGE** (a true scope divergence), while
oneDNN does **not** — L2 confirms the removed exports are internal *and* finds
one genuine public change (`format_tag_last`), so it stays **BREAKING**.

## Timing

- Cost scales with exported-symbol count, as expected: oneTBB (~1 k syms) 0.5 s;
  oneDNN (~mid) 3–4 s; **oneCCL 15 s; oneDAL 52–61 s** (125–149 MB, ~10–25 k
  symbols). All s0; no L4 cliff is paid at this tier.

## L2 finalization (post-PR #444) — scope divergences resolved

The L2 cascade documented above was an **abicheck-side gap, not an environment
limit**: PR #444 (`main` @ `2625ed8`) fixed two defects in the clang L2 backend's
host-toolchain probe — it injected GCC's own compiler-resource dir (breaking on
`__builtin_ia32_*`), and it parsed pure-`#include` umbrella headers in C mode
(so `<cstddef>` was missing). With #444 merged, the clang L2 backend parses these
headers directly (the `-I` include root is still supplied; libstdc++ detection and
the C→C++ retry are now automatic). Re-running the two divergent minor pairs (and
oneTBB as a control) under **public-header scope** confirms the predicted demotions.
Raw data: `data/oneapi_scan_l2_2026-06.json`.

| Lib | Pair | Binary tier (s0) | **L2 (public header)** | What L2 revealed |
|-----|------|------------------|------------------------|------------------|
| oneTBB | 2021.12→2021.13 | COMPATIBLE_WITH_RISK | **COMPATIBLE_WITH_RISK** | Consistent (control); confirms the L2 pipeline. |
| oneDNN | 3.11→3.12 | BREAKING (6) | **BREAKING (1 real)** | 5 findings **demote to risk** — they are *libstdc++* leakage (`std::_Sp_counted_deleter` RTTI/vtable, `std::__do_uninit_copy`, `std::_Hashtable`), not oneDNN API. The 1 remaining breaking change is concrete and **public**: `dnnl::memory::format_tag::format_tag_last` shifted (3.12 added format tags → the enum sentinel changed value). |
| oneDAL | 2025.0→2025.1 | BREAKING (164) | **NO_CHANGE** | Full demotion. The public DAAL API (`daal.h` → **25,595 functions, 1,189 types, 448 enums**) is byte-identical; the 212 removed exports are confirmed **bundled MKL/BLAS** internals (`DGETRF`/`SGETRF`/…) outside oneDAL's public surface. |

**Takeaway:** L2 does exactly what the binary-tier analysis predicted. oneDAL's
minor-bump BREAKING was pure packaging noise → **NO_CHANGE** under public scope.
oneDNN's BREAKING was *mostly* noise (leaked stdlib instantiations demoted to
risk) but L2 also **surfaced one genuine public-API change** the binary tier had
buried among opaque `func_removed_elf_only` rows — the `format_tag_last` enum
sentinel. This is the core value of the header tier: it separates dependency/stdlib
leakage from real public-surface changes. Both `tbb.h` and `daal.h` are pure
`#include` umbrella headers and exercised #444's C→C++ self-heal retry; `dnnl.hpp`
carries inline `namespace`/`enum` and was detected as C++ directly.

## Limitations still open (L3–L5)

- **L3–L5 (s1/s2/s4/s5/s6) need a compile DB.** Not exercised; conda ships no
  `compile_commands.json`, so these require a configure step or a trusted
  `build.query` (oneDAL would use the Bazel `aquery` adapter). L2 settles the
  public/internal boundary for these libraries, so L3–L5 would add build-graph
  provenance rather than change the verdicts above.

## Conclusions

- The scanner runs cleanly on real oneAPI binaries; the Intel-channel harness
  fetches oneCCL correctly.
- Binary-strict `s0` gives the **right verdict on SONAME bumps** and **correctly
  demotes oneTBB's internal-symbol churn** (no FP). The oneDNN/oneDAL minor-bump
  BREAKINGs were **real exported-symbol removals scoped outside each library's
  public API** — the documented scope-divergence case.
- **L2 (post-#444) closes the loop:** oneDAL 2025.0→2025.1 demotes to **NO_CHANGE**
  and oneDNN 3.11→3.12 reduces to **one real public change** (`format_tag_last`),
  confirming zero false positives in the binary tier and demonstrating that the
  header tier both *demotes* dependency/stdlib leakage and *surfaces* genuine
  public-API changes the binary tier cannot name.
