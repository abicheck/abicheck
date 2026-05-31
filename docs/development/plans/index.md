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
| **G1** | [Cross-platform end-to-end validation](g1-cross-platform-e2e.md) | `UC-PLAT-windows-pe`, `UC-PLAT-macos-macho` | L |
| **G2** | [Build-config matrix → `compare`, and bundle completion](g2-build-config-and-bundle.md) | `UC-WF-probe-matrix`, `UC-WF-bundle`, `UC-TC-cxx-standard-floor` | M |
| **G3** | [Workflow-scenario examples & Markdown/HTML coverage](g3-workflow-examples-and-reporting.md) | `UC-REP-markdown-html` | M |
| **G4** | [libclang header-AST extractor](g4-header-ast-extractor.md) | `UC-ARCH-header-only` | XL |
| **G5** | [Plugin host↔plugin contract](g5-plugin-bidirectional-contract.md) | `UC-ARCH-plugin` | M |
| **G6** | [Kernel BTF & accelerator workflows](g6-kernel-btf-and-accelerator.md) | `UC-ARCH-kernel-btf`, `UC-ARCH-sycl` | M |
| **G8** | [Static-library stance](g8-static-libraries.md) | `UC-ARCH-static-lib` | S (decision) |

> G7 (release recommendation) is **done** — see the evaluation doc.

## How to pick up a plan

1. Read the plan and its registry entry/entries.
2. Implement against the **acceptance criteria** (each plan lists them).
3. Flip the registry `status` to `complete` (or a higher tier) and point
   `evidence` at the new tests/examples. The registry test will fail if you
   claim coverage without real evidence — that's the gate that proves the gap
   is actually closed.
4. Update the scorecard row in the evaluation doc.
