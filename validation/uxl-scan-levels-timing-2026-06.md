# UXL `scan`-level run — findings & follow-ups (2026-06)

Real `abicheck scan` 0.3.0 stack run across source-scan levels on two UXL
Foundation libraries, on a **clang-only** host (no castxml). Full per-level
timings/verdicts: `data/uxl_scan_results_2026-06.json`. Timing *guidance*
distilled from this run lives in
[`docs/user-guide/scan-levels.md`](../docs/user-guide/scan-levels.md) and
[`docs/development/performance.md`](../docs/development/performance.md#scan-level-cost-model--one-cliff-at-l4-s4s5)
— this file keeps only the actionable findings.

**Subjects:** oneTBB v2021.12.0→v2021.13.0 (C++, `libtbb.so.12`, 40 TUs);
UMF v0.10.0→v0.11.0 (C, `libumf.so.0`, 50 TUs). Both built `RelWithDebInfo`
with `compile_commands.json`.

## Datapoints worth keeping

- **One cost cliff, at L4 (s4→s5); height is a C++ phenomenon** — oneTBB ~29 s
  (s0–s4) → ~222 s (s5/s6), a ~7× jump; UMF ~17 s → ~21 s, ~1.3×. (→ docs above.)
- **Seed-gated L4 scoping works** — oneTBB `s5` with one `--changed-path` =
  **11.5 s** vs 222 s unseeded, identical verdict.
- **Quality wins (true positives, no FP avalanche):**
  - oneTBB: the old ~90 % DWARF-only false-positive flood (`REPORT.md`) is gone.
    Surface scoping correctly *filtered* internal `tbb::detail::r1::cpu_features_type`
    and *kept* the reachable `thread_request_serializer` (2 breaking / 3 compatible).
  - UMF: genuine v0.10→v0.11 ABI break caught cleanly — 17 real breaks on public
    `umf_*` structs/functions, 104 `symbol_moved_version_node` risks, 0 FPs.
- **Verdict is depth-invariant** — identical at every level on both libs; the
  L0/L1 binary diff sets the gate, L3–L5 only localize.

## oneDAL release binaries (symbols-only scale + FP check)

Two real `libonedal_core.so` releases (pip `daal` wheels; **stripped**, so
L0/L1 symbols-only — no DWARF/L2/L4, internal-leak/P2 not exercised). Raw:
`data/uxl_onedal_release_2026-06.json`.

| Pair | SONAME | Verdict | Time | Findings |
|---|---|---|---:|---|
| 2024.7.0 → 2025.0.0 (major) | `.so.2 → .so.3` | **BREAKING** | 63.6 s | 39,523 — 20,524 breaking (17.5k `func_removed_elf_only`, 3.0k `var_removed`, 2.0k `symbol_binding_strengthened`), 18,969 compatible additions, 30 risk |
| 2025.0.0 → 2025.0.1 (minor) | `.so.3` (same) | **COMPATIBLE** | 50.0 s | **1** — a compatible `visibility_leak`; **zero false breaks** |

- **Scales cleanly:** ~50–64 s on a **125–149 MB** library with **10–25k**
  exported functions; linear, no blow-up (validates the `performance.md`
  detector fixes on the headline oneDAL target).
- **Correct verdicts:** the major `.so.2→.so.3` bump is BREAKING; the
  same-SONAME minor bump is COMPATIBLE with **no false positives** — the
  symbols-only "compare two releases" path remains abicheck's strongest mode.
- Because releases ship stripped, the C++ `detail::`-namespace internal-leak
  path (the P2 fix) is **not** reached here; exercising it on oneDAL needs a
  debug/header build (ties to P1).

## Problems to address

| # | Type | Sev | Status | Problem | Action |
|---|------|-----|--------|---------|--------|
| **P2** | quality | Med | ✅ **fixed (this PR)** | `internal_type_leaks_via_public_api` on oneTBB `thread_request_serializer` reported a size/offset-propagating **layout break**, but its reachability path runs through a `std::unique_ptr` (pointer indirection) — a layout change behind a pointer does **not** change the holder's size/offset. | `internal_leak.py` now suppresses the leak when an internal type is reachable **only** through a pointer and the change is pure layout; identity/vtable changes still fire through a pointer. Regression tests in `test_internal_leak_review.py`. |
| **P1** | bug/feature | High | ⏳ follow-up | L2 header AST is **castxml-only**; clang is installed but unused, so `scan -H` / `dump --headers` hard-fail on a clang-only host — which **cascades into all four D4 cross-source checks skipping**. | Add a **clang L2 backend** (the `--source-abi-extractor clang` path already exists for L4). Sizeable new parser (parallels `dumper_castxml.py`) — own PR/ADR. |
| **P3** | UX | Med | ⏳ **ADR-035 scope** | `--mode pr`/`s5` with no `--since`/`--changed-path` silently replays every TU (== `s6` cost) under a "pr" label. | Default `--since` to merge-base in a git checkout, or warn on an empty changed-set under a `pr`-family mode. Owned by the ADR-035 `scan`-orchestrator workstream (cli_scan.py). |
| **P4** | redundancy | Low | ⏳ follow-up | `s0` and `s3` produce **identical coverage** (L0/L1 + always-on pattern; pinned `s3` adds nothing over the always-on tier). | Documented in scan-levels.md. Level-set reevaluation deferred until ADR-035 is fully implemented (per maintainer). |
| **P5** | UX | Low | ⏳ follow-up | `L4_source_abi` coverage row prints `partial` with an **empty detail string** (no TU count). | Populate the L4 row with replayed/total TU counts. |
| **P6** | noise | Low | ⏳ follow-up | `dump` with no headers emits a `UserWarning` to stderr on every run. | Demote to a single info-level line or gate behind `-v`. |

## Testing follow-ups

- **P2 (done):** `test_internal_leak_review.py::TestPointerMediatedLayoutLeakSuppressed`
  — internal type held by `unique_ptr` in a public type → layout change is **not**
  a leak; the same shape with a vtable change still fires.
- **P1:** once a clang L2 backend exists, an integration test that runs
  `scan -H`/`dump --headers` with **clang only on PATH** (no castxml) and asserts
  the D4 crosschecks run instead of skipping.
- **Level→coverage guard:** assert the `(method, depth)→collected layers` mapping
  (e.g. `s4` ⇒ L3+L5 no L4; `s0`≡`s3` coverage) so the cheap-tier semantics
  documented in scan-levels.md can't silently drift.
