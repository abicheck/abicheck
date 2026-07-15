# Use-Case Coverage Evaluation

**Date:** 2026-06-07
**Purpose:** Evaluate abicheck against the full landscape of application/library
ABI-API change use cases, identify where coverage is deep vs. thin, and record
the concrete code / test / example follow-ups.

This document tracks *uncovered scenarios* (as opposed to undocumented
decisions, which are captured directly in the [ADRs](adr/index.md)).

Three related artifacts, kept distinct: the **examples catalog** (`examples/`)
demonstrates ABI/API *change types*; the **user-scenario catalog**
([User Scenarios & Flows](user-scenarios.md), `tests/scenarios/`) defines *how
users work with abicheck* and drives end-to-end *tool* validation; and the
[plans](plans/index.md) track the *capability backlog*. This document is the
map across all three.

---

## Headline

abicheck is **exceptionally deep on the change-taxonomy axis and comparatively
thin on the breadth axes.** The "what changed" dimension — **352 `ChangeKind`s**
in a 5-tier policy model, **181 calibrated example cases** (134 binary shared-library competitor lanes plus dedicated fixture/source lanes), ABICC + libabigail
parity — is essentially complete and has diminishing returns.

The remaining gaps are **not in detecting more change types**. They are the
three `planned` breadth/workflow items tracked in `usecase-registry.yaml`:
header-only/inline-only analysis (G4), manylinux glibc-floor checks (G10), and
single-binary audit/lint mode (G11) —
plus seven **partial**/`modeled` items with
some shipped work already: auditwheel/delocate vendored-library filename
pairing (G9, `strip_vendor_hash` normalization has shipped; the embedded-SONAME
half for bundle cohort SONAME-skew detection has not), inline-namespace
version-stamp normalization (G15),
header-scoped source-mode toolchain robustness (G16, diagnostics and the
`castxml --version` floor probe have shipped), a real-world validation corpus
(G17), Bazel build-evidence (G18, `modeled` — code exists, not yet validated
end-to-end in CI), the source-scan & cross-source example corpus (G20), and
one-shot deep compare & CLI usability (G21, the `--depth` dial has shipped;
the one-shot orchestrator has not). See "Gaps that matter" and "Proposed next
steps" below for the current state of each.

Several formerly broad gaps are now closed and should no longer be treated as
open roadmap work: native PE/Mach-O compare validation (G1), build-config matrix
integration (G2), workflow/report coverage (G3), plugin host↔plugin checking
(G5), BTF/CTF and SYCL workflows (G6), release recommendations (G7), static
library stance (G8), security-hardening drift (G12), cross-architecture
comparison guardrails (G13), and CPython `abi3` import-contract checking
(G14).

---

## The use-case universe (five axes)

A real invocation is a point in this space:

| Axis | Values |
|---|---|
| **Library archetype** | pure-C system lib · C++ template/vtable lib · header-only/inline · plugin (dlopen) · static (`.a`) · kernel/eBPF · GPU/accelerator (SYCL/CUDA) · FFI-consumed C ABI |
| **Platform** | ELF/Linux · PE+PDB/Windows (MSVC, MinGW) · Mach-O/macOS (x86-64, ARM64) |
| **Change class** | binary ABI break · source API break · compatible addition · quality/bad-practice · deployment risk |
| **Workflow** | CI PR gate · release/package compare · baseline pin · app-compat · multi-lib bundle · build-config matrix · stack/sysroot · Debian symbols · ABICC drop-in · MCP/agent |
| **Toolchain/standard** | GCC/Clang/MSVC/ICX · C++11→23 floor · libstdc++ dual ABI · flag drift · LP64/ILP64 · char8_t/_BitInt/atomic/ABI-tags |

## Coverage scorecard

> **The authoritative, machine-checked status of every use case lives in
> [`usecase-registry.yaml`](usecase-registry.yaml)**, validated by
> `tests/test_usecase_registry.py` (it enforces that coverage claims cite
> evidence paths that actually exist, and that unfinished items carry a tracked
> gap + next steps). The table below is a human snapshot; statuses use the
> registry's vocabulary:
> `complete` · `partial` · `modeled` (code exists, not validated end-to-end) ·
> `planned` · `by_design_excluded`.

| Use case | Status | Notes |
|---|---|---|
| Change taxonomy | `complete` | 352 change kinds; 181 ground-truth entries; parity tests; fixture/source-only L2/L5/source cases are tracked separately from binary `.so` competitor lanes |
| **Release recommendation (semver + SONAME)** | `complete` | semver bump + SONAME action emitted in reports |
| C / C++ archetypes | `complete` | 35 C + 52 C++ example pairs |
| Linux ELF platform | `complete` | the CI-validated baseline |
| Windows PE/MSVC | `complete` | **G1 closed**: `cross-platform-e2e` lane runs `compare` on MinGW DLLs; MSVC+PDB lane asserts struct-growth + removed-export verdicts |
| macOS Mach-O/ARM64 | `complete` | **G1 closed**: `cross-platform-e2e` lane runs `compare` on Apple-clang dylibs; AAPCS64 HFA/HVA + 16-byte boundary modeled + unit-tested |
| `compare`/release/baseline/Debian/ABICC | `complete` | dedicated CLIs + tests |
| MCP server | `complete` | unit-tested (mocks, Linux) |
| Reporting: JSON/SARIF/JUnit | `complete` | versioned schema + 34 SARIF / 55 JUnit tests |
| Reporting: Markdown/HTML | `complete` | structural coverage across verdict tiers + sections + escaping (G3 done) |
| Build-config matrix (`probe`) | `complete` | **G2 closed**: wired into `compare`; both CXX floor and API_DEPENDS proven e2e (`.o` `.symtab` surface capture fixed) |
| Bundle / multi-library | `complete` | all detectors run via `compare-release`; case84 validated e2e (Linux-only by design; cross-platform → G1) |
| Plugin (host↔plugin) | `complete` | **G5 closed**: `plugin-check` CLI + `check_plugin_host_contract` API + plugin_abi policy |
| Security-hardening drift | `complete` | **G12 closed**: full checksec surface (RELRO/BIND_NOW/PIE/canary/FORTIFY/W^X) diffed; shipped `--policy-file security` gate |
| GNU Make / EPICS-style zero-config build evidence | `complete` | **PR #464**: `--sources` auto-runs fixed GNU Make dry-run query when no compile DB exists; CI covers GNU/BSD launcher selection and make/gmake/gnumake/mingw32 transcript parsing |
| Header-only / inline-only | `planned` | castxml can't emit concept bodies / ctor mangled names (G4; cases 78/105/106/111 dormant) |
| Kernel / eBPF (BTF/CTF) | `complete` | **G6 closed**: BTF + CTF struct-change run through `compare`; committed `case121` BTF blobs + bare-blob CLI ingestion + `gcc -gbtf` integration fixture |
| SYCL / accelerator (PI/UR) | `complete` | **G6 closed**: PI *and* UR adapter entrypoint-drop driven through `compare` + reports |
| Static libraries (`.a`/`.lib`) | `by_design_excluded` | **G8 decided (option A)**: non-goal; CLI rejects archives with guidance |
| FFI consumers (Rust/Go/Python) | `by_design_excluded` | C ABI covered; other languages a stated non-goal |

---

## Gaps that matter — current implementation status

| ID | Status | Current state |
|---|---|---|
| **G1** | ✅ closed | Native PE/Mach-O `compare` is validated in CI; MSVC+PDB has a dedicated non-blocking lane. |
| **G2** | ✅ closed | Build matrices fold into `compare`/`compare-release` via `--probe-matrix old=/new=`; C++ floor and environment-dependent API findings are end-to-end tested. |
| **G3** | ✅ closed | Workflow scenarios and Markdown/HTML report coverage are validated beyond single-pair `compare`. |
| **G4** | planned | Header-only / inline-only libraries still need a libclang header-AST extractor. |
| **G5** | ✅ closed | `plugin-check` and `check_plugin_host_contract` cover host↔plugin load contracts. |
| **G6** | ✅ closed | BTF/CTF and SYCL PI/UR workflows run through `compare` and reports. |
| **G7** | ✅ closed | Semver bump and SONAME action recommendations are emitted by the report layer. |
| **G8** | by-design excluded | Static/import archives are rejected with guidance; archive member API checking is a non-goal. |
| **G9** | partial | Filename-based vendored-library pairing shipped (`strip_vendor_hash` in `compare-release`'s matching pass) — a bundled `libpng16-<hash>.so.16.x` now pairs across rebuilds instead of removed+added noise, and a real break in the paired dependency still surfaces. Remaining: normalize the embedded ELF SONAME/install-name for `bundle.py`'s cohort-scoped SONAME-skew detector. |
| **G10** | planned | manylinux glibc-floor / platform-baseline checks. |
| **G11** | planned | Single-binary ABI audit/lint mode. |
| **G12** | ✅ closed | Security-hardening drift captures and diffs RELRO, BIND_NOW, PIE, canaries, FORTIFY, and W^X metadata; the security policy is shipped. |
| **G13** | ✅ closed | ELF snapshot captures `e_machine`/`EI_CLASS`/endianness; a mismatch is a hard guard (`ELF_MACHINE_CHANGED`/`ELF_CLASS_CHANGED`, `BREAKING_KINDS`) rather than a false-green `COMPATIBLE_WITH_RISK` verdict. |
| **G14** | ✅ closed | CPython Limited-API / `abi3` import-contract conformance — extension recognition, `abi3`/Limited-API import-contract check, `scan --abi3` audit. |
| **G15** | partial | Inline-namespace version-stamp normalization for ICU/Abseil/libstdc++-style churn. Detector landed (advisory `versioned_symbol_scheme_detected`); normalize-and-collapse preset still planned. |
| **G17** | partial | Real-world upstream-library validation corpus (`eval/manifest.yaml` + `runner.py`, conda-forge fetch + `dump`/`compare`) — reproducible but not continuously run in CI; complements the synthetic `examples/case*` fixtures. |
| **G18** | modeled | Bazel L3 build-evidence adapter (`buildsource/adapters/bazel.py`, `cquery`/`aquery` jsonproto → `BuildEvidence`) exists but has never been validated end-to-end on a real Bazel C++ project (blocked on oneDAL's Bazel + legacy-makefile, no-CMake toolchain). |
| **G19** | complete | PR-tier source intelligence (ADR-035, D1–D10): always-on compiler-free pre-scan + risk-scored escalation, intra-version cross-source validation findings (six checks + FP-rate-gate corpus), single-release hygiene audit, evidence-directed scan focusing, build-emitted source-facts protocol, and a typed `run_scan`/`ScanResult` API + per-level provider protocol with per-project cost estimate. |
| **G20** | partial | Source-scan & cross-source example corpus (ADR-035 demonstration): single-release audit cases, cross-source corroboration cases (combination beats any single source), and evidence-directed focusing scenarios. Grows the `examples/` catalog + test suites to demonstrate the G19 engine; no engine change. |
| **G21** | partial | One-shot deep compare + CLI usability (oneDAL eval). **Shipped (PR #422):** the `--depth headers\|build\|graph\|source\|full` dial (`--max`=full, reusing the `scan --depth` vocabulary) on `dump`; rich-click option-group `--help` panels (collapse M1); and the strict-mode honesty fix (empty requested L4 → `skipped`). **Remaining:** the one-shot `compare` orchestrator (dump both sides with `--sources`, then compare) + header/source auto-discovery, a cross-platform list-threaded `--gcc-option`, `compile_commands.json` auto-synthesis, a fail-loud signal on an empty requested layer, and vocab unification (M5). |
| **G22** | ✅ closed | CLI interface contract, config balance, and extension policy ([ADR-037](adr/037-cli-interface-contract.md)). Followed G21's depth dial with the structural cleanup the flag-divergence audit surfaced: three named tiers with `service.py` as the only compare chokepoint (fixes `compare-release` bypassing it with a different `scope_public` default), typed `CompareRequest` dataclasses, one decorator per shared option family (kills the severity/header/policy/debug copy-paste drift), a single `--depth` vocabulary (drops the "evidence" naming and the user-facing L5-graph rung), folding `compare-release`/`deep-compare` into `compare`, `--header-backend` → `--ast-frontend`, a CLI↔`.abicheck.yml` rebalance, an explicit `--exit-code-scheme`, and a `cli-contract` CI gate. Backward-compat mechanism designed, left advisory until 1.0. |
| **G16** | partial | Header-scoped source-mode toolchain robustness. Surfaced by 21 real-world cron records. **Shipped**: actionable diagnostics for all three host-toolchain signatures (sized-float `_FloatN`, GCC `__assume__`, `--lang c` + `extern "C"`), plus a `castxml --version` probe that recommends the Clang floor (≥ 18) on a version-mismatch failure. A `-D_FloatN` shim was prototyped and **rejected** (it rewrites glibc's own `typedef float _Float32;` fallback); the durable cure is a newer-Clang castxml or the libclang extractor (G4). **Remaining**: real-host end-to-end check and a dedicated error type. |

## Proposed next steps (tracked in the registry)

The authoritative backlog is the set of `planned` entries in
[`usecase-registry.yaml`](usecase-registry.yaml). Each entry carries a `gap`, a
plan file, and concrete `next_steps`; `tests/test_usecase_registry.py` prevents a
planned row from drifting away from its plan.

| Priority | Gap | Plan |
|---|---|---|
| High | G9 — wheel vendored-library pairing | [g9](plans/g9-wheel-vendored-matching.md) |
| Medium | G4 — header-only / inline-only analysis | [g4](plans/g4-header-ast-extractor.md) |
| Medium | G11 — single-binary audit/lint | [g11](plans/g11-single-binary-audit.md) |
| Medium | G15 — inline-namespace version stamp | [g15](plans/g15-inline-namespace-version.md) |
| Small | G10 — glibc-floor check | [g10](plans/g10-glibc-floor-check.md) |
| Medium | G16 — header-scope toolchain robustness | [g16](plans/g16-header-scope-toolchain-robustness.md) |
| Medium | G17 — real-world validation corpus | [g17](plans/g17-real-world-corpus.md) |
| Medium | G18 — Bazel build-evidence | [g18](plans/g18-bazel-build-evidence.md) |
| Medium | G20 — source-scan & cross-source example corpus | [g20](plans/g20-source-scan-example-catalog.md) |
| Medium | G21 — one-shot deep compare & CLI usability | [g21](plans/g21-oneshot-deep-compare.md) |
