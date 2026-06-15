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

## Problems to address

| # | Type | Sev | Problem | Suggested action |
|---|------|-----|---------|------------------|
| **P1** | bug/feature | High | L2 header AST is **castxml-only**; clang is installed but unused, so `scan -H` / `dump --headers` hard-fail on a clang-only host — which **cascades into all four D4 cross-source checks skipping** (`exported_not_public`, `public_not_exported`, `header_build_context_mismatch`, `private_header_leak`). | Add a **clang L2 backend** (the `--source-abi-extractor clang` path already exists for L4). Track + test on a clang-only host. |
| **P2** | quality | Med | `internal_type_leaks_via_public_api` on oneTBB `thread_request_serializer` reports a size/offset-propagating **layout break**, but its reachability path runs through a `std::unique_ptr` member (pointer indirection) — a field change behind a pointer does **not** change the holder's size/offset. Either over-aggressive or mis-described. | Path classifier should distinguish **by-value/inheritance** embedding from **behind-pointer** reachability; fix the rationale text. **Add regression fixture:** internal type held by `unique_ptr` in a public type → not a size-propagating BREAKING. |
| **P3** | UX | Med | `--mode pr`/`s5` with no `--since`/`--changed-path` silently replays every TU (== `s6` cost) under a "pr" label. | Default `--since` to the repo's merge-base when in a git checkout, **or** warn when a `pr`-family mode runs with an empty changed-set. |
| **P4** | redundancy | Low | `s0` and `s3` produce **identical coverage** (L0/L1 + always-on pattern; pinned `s3` adds nothing over the always-on tier). Cheap tier has no cost differentiation. | Document level selection (done — scan-levels.md) and consider collapsing/aliasing `s3`, or give pinned `s3` a distinct deliverable. |
| **P5** | UX | Low | `L4_source_abi` coverage row prints `partial` with an **empty detail string** (no TU count / hit-miss), unlike the L3 row ("40 compile units"). | Populate the L4 row with replayed/total TU counts. |
| **P6** | noise | Low | `dump` with no headers emits a `UserWarning` to stderr on every run. | Demote to a single info-level line or gate behind `-v`. |

## Testing follow-ups

- **P2 regression:** fixture pair where a public type holds an internal type via
  `std::unique_ptr`; assert it is **not** classified as size/offset-propagating
  BREAKING (guards the reachability-path classifier).
- **P1:** once a clang L2 backend exists, an integration test that runs
  `scan -H`/`dump --headers` with **clang only on PATH** (no castxml) and asserts
  the D4 crosschecks run instead of skipping.
- **Level→coverage guard:** assert the `(method, depth)→collected layers` mapping
  (e.g. `s4` ⇒ L3+L5 no L4; `s0`≡`s3` coverage) so the cheap-tier semantics
  documented in scan-levels.md can't silently drift.
