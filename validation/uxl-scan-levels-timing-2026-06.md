# UXL `scan`-level run — findings & follow-ups (2026-06)

Real `abicheck scan` 0.3.0 stack run across source-scan levels on two UXL
Foundation libraries (clang-only host, no castxml), plus two oneDAL release
binaries. Timing **guidance** lives in
[`docs/user-guide/scan-levels.md`](../docs/user-guide/scan-levels.md) and
[`docs/development/performance.md`](../docs/development/performance.md#scan-level-cost-model-one-cliff-at-l4);
raw per-run data in `data/uxl_scan_results_2026-06.json` and
`data/uxl_onedal_release_2026-06.json`. This file keeps only the final
conclusions + the open-problem tracker.

**Subjects:** oneTBB v2021.12.0→v2021.13.0 (C++, `libtbb.so.12`, 40 TUs);
UMF v0.10.0→v0.11.0 (C, `libumf.so.0`, 50 TUs); oneDAL `libonedal_core` 2024.7.0
→ 2025.0.0 (`.so.2`→`.so.3`) and 2025.0.0 → 2025.0.1 (release `.so`, stripped).

## Conclusions

- **Timing: one cost cliff at the L4 AST boundary (s4→s5); its height tracks C++
  template depth** — oneTBB ~29 s → ~222 s (~7×), UMF ~17 s → ~21 s (~1.3×). The
  cheap tier (s0–s4) is one price; `s5`/`--mode pr` only beats `s6` **with** a
  `--since`/`--changed-path` seed (222 s → 11.5 s, identical verdict). Verdict is
  depth-invariant — L0/L1 sets the gate, L3–L5 only localize.
- **Quality (true positives, no FP avalanche):** oneTBB's old ~90 % DWARF-only
  false-positive flood is gone (surface scoping filters internal
  `tbb::detail::r1::cpu_features_type`); UMF's real v0.10→v0.11 ABI break is caught
  cleanly (17 real breaks, 0 FPs); oneDAL scales linearly (~50–64 s on 125–149 MB
  / 10–25k symbols) with the correct major=BREAKING / minor=COMPATIBLE verdicts
  and **zero false breaks** on the minor bump.

## Open problems & current status

(after rebase onto finalized ADR-035 — D4/D8 cross-checks, D7 POI, D10 providers,
`scan --estimate`.)

| # | Sev | Status | Problem & current state |
|---|-----|--------|-------------------------|
| **P1** | High | ✅ fixed (first slice) | **L2 header AST was castxml-only** — `scan -H` / `dump --headers` hard-failed on a clang-only host. Now a clang L2 backend (`dumper_clang._ClangAstParser`, `clang -ast-dump=json` → `AbiSnapshot`) sits behind a `--header-backend auto\|castxml\|clang` knob (env `ABICHECK_HEADER_BACKEND`); `auto` falls back to clang when only clang is on `PATH`, so header-aware scoping + the cross-checks run. See **ADR-003 → "Extension: clang as an alternative L2 frontend (implemented)"**. Layout (`size`/`offsets`/vtable) is still castxml/DWARF-only — clang's JSON AST is syntactic. *(D8 `unversioned_exported_symbol` runs header-free regardless.)* |
| **P2** | Med | ✅ fixed (per-hop rework) | `internal_type_leaks_via_public_api` over-reported a pure-layout change to an internal type reached only through a pointer. Indirection is now recorded **per edge at enqueue time** (per template argument), so a layout change behind a pointer is demoted while a by-value member/inheritance keeps the finding and identity/vtable changes still fire. Closes the oneTBB `thread_request_serializer` case — including libstdc++'s *decomposed* `unique_ptr` (`_Tuple_impl`/`_Head_base`, pointer as a nested template arg): the real oneTBB v2021.12→.13 compare is now **COMPATIBLE** (was a false BREAKING). `pair<Impl,int*>` and other by-value members are unaffected. |
| **P3** | Med | ◔ ADR-035 scope | Seedless `--mode pr`/`s5` replays every TU (== `s6` cost). **Now visible** via `scan --estimate` (prints `replay scope (N of N TU(s))` + projected total up front); no auto-warn/auto-seed yet. Owned by the ADR-035 orchestrator workstream. |
| **P4** | Low | ⏳ open | `s0` ≡ `s3` coverage (L0/L1 + always-on pattern). Documented in scan-levels.md; level-set reevaluation deferred until ADR-035 work settles (per maintainer). |
| **P5** | Low | ⏳ open | `L4_source_abi` coverage row prints `partial` with an empty detail string. `scan --estimate` now reports TU counts; the live coverage row should too. |
| **P6** | Low | ⏳ open | `dump` with no headers emits a `UserWarning` to stderr every run — demote to info / gate behind `-v`. |

Other note: **s2** (preprocessor) is now implemented (`--source-method s2` runs,
`depth=build`); on the oneTBB build `clang -E` failed every invocation (the `-E`
pass needs the TU's full flag set), so no preprocessor facts were produced there.

## Testing follow-ups

- **P2 (done):** `test_internal_leak_review.py` — direct pointer field / pointer
  typedef / opaque-handle pointer param suppressed; by-value, `pair<Impl,int*>`,
  `Pimpl`-named records, and nested-behind-pointer shapes still fire.
- **P1:** once a clang L2 backend exists, an integration test running `scan -H`
  with **clang only on PATH** that asserts the cross-checks run, not skip.
- **Level→coverage guard:** assert the `(method, depth)→collected layers` mapping
  (`s4` ⇒ L3+L5 no L4; `s0`≡`s3`) so the documented cheap-tier semantics can't drift.
