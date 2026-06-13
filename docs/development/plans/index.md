# Implementation Plans

Detailed, actionable plans for the **remaining** use-case gaps identified in the
[Use-Case Coverage Evaluation](../usecase-coverage-evaluation.md). Each gap in
[`usecase-registry.yaml`](../usecase-registry.yaml) whose status is `partial`,
`modeled`, or `planned` links to one of these plans via its `plan:` field, and
`tests/test_usecase_registry.py` enforces that the linked plan file exists.

Each plan follows the same template: **Problem · Goal & acceptance criteria ·
Design · Files & surfaces · Tests · Example fixtures · Effort & risk · Out of
scope**.

| Gap | Plan | Registry use cases | Effort |
|---|---|---|---|
| **G4** | [libclang header-AST extractor](g4-header-ast-extractor.md) | `UC-ARCH-header-only` | XL |
| **G9** | [manylinux/auditwheel vendored-library pairing](g9-wheel-vendored-matching.md) | `UC-WF-wheel-vendored` | M |
| **G10** | [manylinux glibc-floor check](g10-glibc-floor-check.md) | `UC-TC-glibc-floor` | S |
| **G11** | [Single-binary ABI audit / lint](g11-single-binary-audit.md) | `UC-WF-audit` | M |
| **G13** | [Cross-architecture comparison guardrail](g13-arch-mismatch-guard.md) | `UC-PLAT-arch-guard` | S |
| **G14** | [CPython Limited-API / `abi3` import-contract](g14-stable-abi-subset.md) | `UC-WF-stable-abi-subset` | M |
| **G15** | [Inline-namespace version-stamp normalization](g15-inline-namespace-version.md) | `UC-CHANGE-inline-ns-version` | M |
| **G16** | [Header-scope toolchain robustness](g16-header-scope-toolchain-robustness.md) | `UC-TC-header-scope-robustness` | M |
| **G17** | [Real-world validation corpus](g17-real-world-corpus.md) | `UC-WORKFLOW-real-world-corpus` | M |
| **G18** | [Bazel build-evidence](g18-bazel-build-evidence.md) | `UC-TC-bazel-build-evidence` | M |

Completed or decided plans are retained for implementation history:

| Gap | State | Reference |
|---|---|---|
| **G1** | Done — native PE/Mach-O compare validation and non-blocking MSVC+PDB lane | [g1](g1-cross-platform-e2e.md) |
| **G2** | Done — build matrix folds into `compare`/`compare-release`; bundle soname-skew is wired | [g2](g2-build-config-and-bundle.md) |
| **G3** | Done — workflow scenarios and Markdown/HTML coverage | [g3](g3-workflow-examples-and-reporting.md) |
| **G5** | Done — `plugin-check` CLI and host↔plugin API | [g5](g5-plugin-bidirectional-contract.md) |
| **G6** | Done — BTF/CTF and SYCL PI/UR workflows | [g6](g6-kernel-btf-and-accelerator.md) |
| **G7** | Done — release recommendation | `abicheck/semver.py` |
| **G8** | Decided — static/import archives are a by-design non-goal | [g8](g8-static-libraries.md) |
| **G12** | Done — security-hardening drift surface and policy preset | [g12](g12-security-hardening.md) |

## How to pick up a plan

1. Read the plan and its registry entry/entries.
2. Implement against the **acceptance criteria** (each plan lists them).
3. Flip the registry `status` to `complete` (or a higher tier) and point
   `evidence` at the new tests/examples. The registry test will fail if you
   claim coverage without real evidence — that's the gate that proves the gap
   is actually closed.
4. Update the scorecard row in the evaluation doc.
