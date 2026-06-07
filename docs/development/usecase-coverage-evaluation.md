# Use-Case Coverage Evaluation

**Date:** 2026-06-07
**Purpose:** Evaluate abicheck against the full landscape of application/library
ABI-API change use cases, identify where coverage is deep vs. thin, and record
the concrete code / test / example follow-ups.

This is a companion to [`adr/adr-gap-analysis.md`](adr/adr-gap-analysis.md)
(which tracks *undocumented decisions*); this document tracks *uncovered
scenarios*.

Three related artifacts, kept distinct: the **examples catalog** (`examples/`)
demonstrates ABI/API *change types*; the **user-scenario catalog**
([User Scenarios & Flows](user-scenarios.md), `tests/scenarios/`) defines *how
users work with abicheck* and drives end-to-end *tool* validation; and the
[plans](plans/index.md) track the *capability backlog*. This document is the
map across all three.

---

## Headline

abicheck is **exceptionally deep on the change-taxonomy axis and comparatively
thin on the breadth axes.** The "what changed" dimension — **192 `ChangeKind`s**
in a 5-tier policy model, **126 calibrated example cases**, ABICC + libabigail
parity — is essentially complete and has diminishing returns.

The remaining gaps are **not in detecting more change types**. They are the
seven planned breadth/workflow items tracked in `usecase-registry.yaml`:
header-only/inline-only analysis (G4), auditwheel vendored-library pairing (G9),
manylinux glibc-floor checks (G10), single-binary audit/lint mode (G11),
cross-architecture guardrails (G13), CPython `abi3` import-contract checking
(G14), and inline-namespace version-stamp normalization (G15).

Several formerly broad gaps are now closed and should no longer be treated as
open roadmap work: native PE/Mach-O compare validation (G1), build-config matrix
integration (G2), workflow/report coverage (G3), plugin host↔plugin checking
(G5), BTF/CTF and SYCL workflows (G6), release recommendations (G7), static
library stance (G8), and security-hardening drift (G12).

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
| Change taxonomy | `complete` | 192 kinds; 126 cases; parity tests |
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
| **G2** | ✅ closed | Build matrices fold into `compare`/`compare-release` via `--probe-matrix-old/new`; C++ floor and environment-dependent API findings are end-to-end tested. |
| **G3** | ✅ closed | Workflow scenarios and Markdown/HTML report coverage are validated beyond single-pair `compare`. |
| **G4** | planned | Header-only / inline-only libraries still need a libclang header-AST extractor. |
| **G5** | ✅ closed | `plugin-check` and `check_plugin_host_contract` cover host↔plugin load contracts. |
| **G6** | ✅ closed | BTF/CTF and SYCL PI/UR workflows run through `compare` and reports. |
| **G7** | ✅ closed | Semver bump and SONAME action recommendations are emitted by the report layer. |
| **G8** | by-design excluded | Static/import archives are rejected with guidance; archive member API checking is a non-goal. |
| **G9** | planned | auditwheel/delocate vendored-library hashed SONAME normalization. |
| **G10** | planned | manylinux glibc-floor / platform-baseline checks. |
| **G11** | planned | Single-binary ABI audit/lint mode. |
| **G12** | ✅ closed | Security-hardening drift captures and diffs RELRO, BIND_NOW, PIE, canaries, FORTIFY, and W^X metadata; the security policy is shipped. |
| **G13** | planned | Cross-architecture mismatch guardrail. |
| **G14** | planned | CPython Limited-API / `abi3` import-contract conformance. |
| **G15** | planned | Inline-namespace version-stamp normalization for ICU/Abseil/libstdc++-style churn. |

## Proposed next steps (tracked in the registry)

The authoritative backlog is the set of `planned` entries in
[`usecase-registry.yaml`](usecase-registry.yaml). Each entry carries a `gap`, a
plan file, and concrete `next_steps`; `tests/test_usecase_registry.py` prevents a
planned row from drifting away from its plan.

| Priority | Gap | Plan |
|---|---|---|
| High | G9 — wheel vendored-library pairing | [g9](plans/g9-wheel-vendored-matching.md) |
| High | G14 — CPython `abi3` import-contract | [g14](plans/g14-stable-abi-subset.md) |
| Medium | G4 — header-only / inline-only analysis | [g4](plans/g4-header-ast-extractor.md) |
| Medium | G11 — single-binary audit/lint | [g11](plans/g11-single-binary-audit.md) |
| Medium | G15 — inline-namespace version stamp | [g15](plans/g15-inline-namespace-version.md) |
| Small | G10 — glibc-floor check | [g10](plans/g10-glibc-floor-check.md) |
| Small | G13 — cross-architecture guardrail | [g13](plans/g13-arch-mismatch-guard.md) |
