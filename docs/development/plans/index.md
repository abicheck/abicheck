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
| **G4** | [libclang header-AST extractor](g4-header-ast-extractor.md) | `UC-ARCH-header-only` | XL |
| **G6** | [Kernel BTF & accelerator workflows](g6-kernel-btf-and-accelerator.md) | `UC-ARCH-kernel-btf`, `UC-ARCH-sycl` | M |
| **G9** | [manylinux/auditwheel vendored-library pairing](g9-wheel-vendored-matching.md) | `UC-WF-wheel-vendored` | M |
| **G10** | [manylinux glibc-floor check](g10-glibc-floor-check.md) | `UC-TC-glibc-floor` | S |
| **G11** | [Single-binary ABI audit / lint](g11-single-binary-audit.md) | `UC-WF-audit` | M |
| **G12** | [Security-hardening drift surface + preset](g12-security-hardening.md) | `UC-WF-security-hardening` | M |
| **G13** | [Cross-architecture comparison guardrail](g13-arch-mismatch-guard.md) | `UC-PLAT-arch-guard` | S |
| **G14** | [CPython Limited-API / `abi3` import-contract](g14-stable-abi-subset.md) | `UC-WF-stable-abi-subset` | M |
| **G15** | [Inline-namespace version-stamp normalization](g15-inline-namespace-version.md) | `UC-CHANGE-inline-ns-version` | M |

> **G3** (workflow-scenario examples & Markdown/HTML coverage) is **done** —
> see [`g3-workflow-examples-and-reporting.md`](g3-workflow-examples-and-reporting.md)
> and the evaluation doc. **G2** ([build-config matrix](g2-build-config-and-bundle.md))
> is **done**: the matrix folds into `compare`/`compare-release`, the bundle
> soname-skew is wired + validated, and both `CXX_STANDARD_FLOOR_RAISED` and
> `API_DEPENDS_ON_CONSUMER_ENV` fire end-to-end (the latter unblocked by the
> relocatable-object `.symtab` surface capture). **G7** (release recommendation)
> is **done** too.
> **G8** ([static-library stance](g8-static-libraries.md)) is **decided**
> (option A — non-goal): the CLI now detects `.a`/`.lib` archives and rejects
> them with guidance, and `UC-ARCH-static-lib` is `by_design_excluded`.
> **G5** ([plugin host↔plugin contract](g5-plugin-bidirectional-contract.md)) is
> **done**: the `plugin-check` CLI + `check_plugin_host_contract` API close the
> dlopen direction, and `UC-ARCH-plugin` is `complete`.

## How to pick up a plan

1. Read the plan and its registry entry/entries.
2. Implement against the **acceptance criteria** (each plan lists them).
3. Flip the registry `status` to `complete` (or a higher tier) and point
   `evidence` at the new tests/examples. The registry test will fail if you
   claim coverage without real evidence — that's the gate that proves the gap
   is actually closed.
4. Update the scorecard row in the evaluation doc.
