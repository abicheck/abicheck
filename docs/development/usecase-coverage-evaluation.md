# Use-Case Coverage Evaluation

**Date:** 2026-05-30
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
thin on the breadth axes.** The "what changed" dimension — **183 `ChangeKind`s**
in a 5-tier policy model, **121 calibrated example cases**, ABICC + libabigail
parity — is essentially complete and has diminishing returns.

The remaining gaps are **not in detecting more change types**. They are in
**breadth across platforms, workflows, and consumption topologies**:

1. Cross-platform support is *modeled but unvalidated end-to-end* — ~95% of
   workflow tests are Linux; there are **no Windows/macOS workflow integration
   tests**, and 20 example cases carry platform `known_gap` notes concentrated
   on Windows/macOS.
2. The build-configuration matrix is **siloed in a separate `probe` command**
   disconnected from `compare` / `compare-release`.
3. The example catalog is **almost entirely single-pair `compare` fixtures** —
   the other workflows (`appcompat`, `deps`/`stack-check`, `bundle`, `probe`)
   are unit-tested with synthetic snapshots, not driven by catalog fixtures.

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
| Change taxonomy | `complete` | 183 kinds; 121 cases; parity tests |
| **Release recommendation (semver + SONAME)** | `complete` | **added in this change** |
| C / C++ archetypes | `complete` | 35 C + 52 C++ example pairs |
| Linux ELF platform | `complete` | the CI-validated baseline |
| Windows PE/MSVC | `partial` | MSVC+PDB e2e lane (`test_msvc_pdb_e2e.py`); MinGW experimental |
| macOS Mach-O/ARM64 | `modeled` | parsers + unit tests; no e2e; ARM64 HFA/HVA not tracked |
| `compare`/release/baseline/Debian/ABICC | `complete` | dedicated CLIs + tests |
| MCP server | `complete` | unit-tested (mocks, Linux) |
| Reporting: JSON/SARIF/JUnit | `complete` | versioned schema + 34 SARIF / 55 JUnit tests |
| Reporting: Markdown/HTML | `complete` | structural coverage across verdict tiers + sections + escaping (G3 done) |
| Build-config matrix (`probe`) | `partial` | wired into `compare` via `--probe-matrix-old/new`; CXX floor proven e2e; API_DEPENDS still blocked on `.o` surface capture (G2) |
| Bundle / multi-library | `complete` | all detectors run via `compare-release`; case84 validated e2e (Linux-only by design; cross-platform → G1) |
| Plugin (host↔plugin) | `partial` | policy + scenario tests; no bidirectional CLI/fixture (G5) |
| Header-only / inline-only | `planned` | castxml can't emit concept bodies / ctor mangled names (G4; cases 78/105/106/111 dormant) |
| Kernel / eBPF (BTF/CTF) | `modeled` | parsers exist; no workflow/example (G6) |
| Static libraries (`.a`/`.lib`) | `planned` | scope decision pending (G8) |
| FFI consumers (Rust/Go/Python) | `by_design_excluded` | C ABI covered; other languages a stated non-goal |

---

## Gaps that matter — and what each needs

| ID | Gap | Code | Tests | Examples |
|---|---|:--:|:--:|:--:|
| **G1** | Cross-platform is aspirational, not validated (Win/macOS) | ARM64 AAPCS, MSVC mangling fidelity | PE/Mach-O **e2e** in CI | label tags honestly |
| **G2** | Build-config matrix siloed in `probe` | ✅ folded into `compare`/`compare-release` (`--probe-matrix-old/new`); bundle soname-skew wired | ✅ CXX floor e2e + case84 bundle e2e (`API_DEPENDS` e2e still pending `.o` surface capture) | ✅ `feature_macro.yaml`, `cxx_standard.yaml` |
| **G3** | Catalog only exercises `compare`; Markdown/HTML test coverage thin | — | ✅ appcompat-from-catalog + stack-check sysroot e2e + Markdown/HTML structural coverage | scenarios asserted in new tests |
| **G4** | Header-only / inline-only (detector frontier) | libclang header-AST extractor | unblock cases 78/105/106/111 | reuse dormant fixtures |
| **G5** | Plugin host↔plugin contract is one-directional | optional host-contract check | bidirectional scenario | host/plugin fixture |
| **G6** | Kernel/eBPF use case is parser-only | small workflow glue | BTF compare scenario | vmlinux/module fixture |
| **G7** | No semver-bump recommendation | recommender + report wiring | mapping + integration | reuse cases |
| **G8** | Static libraries undocumented | (optional `ar` iteration) | — | document the stance |

### Answer to the four driving questions

1. **How does abicheck handle these configurations?** Superbly for *change-type
   detection on Linux via `compare`*; partially-to-aspirationally for
   *Windows/macOS, build-config-dependent APIs, plugin/kernel topologies, and
   "what should I version-bump?"*.
2. **Code changes needed?** Yes, but mostly **breadth/integration**, not new
   detectors: semver output (S, **done here**), probe-into-compare wiring (M),
   plugin contract (M), ARM64/MSVC fidelity (L), libclang AST (XL).
3. **More tests?** **The most clearly justified item** — cross-platform e2e and
   workflow-level (non-`compare`) tests.
4. **Examples worth adding?** Yes, but of a **different kind** — the catalog is
   saturated with change-*types*; the high-value additions are
   *workflow/topology* scenarios (plugin pair, kernel BTF, probe matrix,
   appcompat/stack scenarios) and **native PE/Mach-O fixtures**.

---

## What is implemented vs. planned

To be unambiguous: this work **fully implemented one cell** (the semver/SONAME
recommender), **partially advanced two** (the plugin contract — via scenario
tests; cross-platform — via an honesty doc + guard), and **left the rest as
tracked plans** (`planned`/`partial`/`modeled` rows in
[`usecase-registry.yaml`](usecase-registry.yaml), each with a gap id and
`next_steps`). Nothing else from the scorecard was silently "finished" — the
registry test would fail if a status claimed evidence that did not exist.

### Implemented in this change

This PR lands the highest value-per-effort slice and the scaffolding for the
rest:

- **G7 — Release recommender** (`abicheck/semver.py`): maps the policy-aware
  verdict + change set to a recommended **semver bump** (`major`/`minor`/
  `patch`/`none`) and a **SONAME action** (`bump_required`/`bump_performed`/
  `bump_missing`/`no_bump_needed`). Always present in `--format json` under
  `release_recommendation`; opt-in for Markdown via `--recommend`. Unit-tested
  in `tests/test_semver_recommendation.py`.
- **G3 / G5 — Workflow-scenario tests** (`tests/test_workflow_scenarios.py`):
  drop-in upgrade gate, additive minor release, host↔plugin load contract (both
  directions of "does this drop break *this* consumer"), and a policy-scoped
  release decision — covering topologies that `compare` alone does not express.
- **G1 — Cross-platform honesty**: the platform-support reality (Linux =
  CI-validated baseline; Windows/macOS = parser-level, partial) is stated in
  [`reference/platforms.md`](../reference/platforms.md) and guarded by
  `tests/test_platform_coverage_honesty.py`, which enforces that every example
  case supports the Linux baseline and that Windows/macOS remain a strict
  subset.

## Proposed next steps (tracked, not in this change)

> **Detailed, actionable plans for every remaining item live in
> [`plans/`](plans/index.md)** — one per gap, each with goal, acceptance
> criteria, design, files to touch, test plan, and effort. Each `partial` /
> `modeled` / `planned` entry in [`usecase-registry.yaml`](usecase-registry.yaml)
> links its plan via a `plan:` field that the registry test verifies exists.

Summary (see the plans for detail):

- **G1 (CI):** add Windows (MinGW) and macOS smoke jobs that run `compare` /
  `appcompat` on a handful of native PE/Mach-O fixtures; promote the most
  reliable `known_gap` cases to validated once green.
- **G2:** an opt-in `compare --probe-spec spec.yaml` that runs the matrix
  harness and folds `API_DEPENDS_ON_CONSUMER_ENV` /
  `CXX_STANDARD_FLOOR_RAISED` / `BEHAVIOURAL_DEFAULT_CHANGED` into the verdict.
- **G4:** a libclang-based header-AST extractor alongside castxml to unblock
  concept tightening, hidden friends, and user-ctor mangled names (cases
  78/105/106/111).
- **G6:** a BTF fixture pair (kernel struct gains a field) exercised through
  `compare`, plus a documented "module vs `vmlinux` BTF" workflow.
- **G8:** decide whether `.a`/`.lib` archive iteration is in scope; document the
  outcome either way.
