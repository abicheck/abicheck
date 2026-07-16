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
| **G11** | [Single-binary ABI audit / lint](g11-single-binary-audit.md) | `UC-WF-audit` | M |
| **G15** | [Inline-namespace version-stamp normalization](g15-inline-namespace-version.md) | `UC-CHANGE-inline-ns-version` | M |
| **G17** | [Real-world validation corpus](g17-real-world-corpus.md) | `UC-WORKFLOW-real-world-corpus` | M |
| **G18** | [Bazel build-evidence](g18-bazel-build-evidence.md) | `UC-TC-bazel-build-evidence` | M |
| **G20** | [Source-scan & cross-source example corpus](g20-source-scan-example-catalog.md) | `UC-WORKFLOW-audit-example-corpus`, `UC-CHANGE-crosscheck-example-corpus`, `UC-WORKFLOW-focusing-example-corpus` | L |
| **G21** | [One-shot deep compare & CLI usability](g21-oneshot-deep-compare.md) | `UC-WF-oneshot-deep` | M |
| **G25** | [Cython API/ABI frontend](g25-cython-api-abi-frontend.md) | `UC-ARCH-cython-api` | XL |
| **G26** | [NumPy C-API compatibility envelope](g26-numpy-capi-envelope.md) | `UC-TC-numpy-capi-envelope` | L |
| **G27** | [Wheel tag / deployment-claim verification](g27-wheel-deployment-verification.md) | `UC-TC-wheel-deployment-claims` | L |

Initiative plans (cross-cutting, not tied to a single registry gap):

| Plan | ADR | Effort |
|---|---|---|
| **G19** | [PR-tier source intelligence & cross-source validation](g19-pr-source-intelligence.md) | [ADR-035](../adr/035-pr-tier-source-intelligence-and-crosscheck.md) · XL (phased) |
| **G24** | [Linux ABI/API detection gap closure](g24-linux-abi-gap-closure.md) | — · L (phased: ELF facts → vtable machinery → clang flag extraction → kABI/ecosystem; macOS/Windows gaps recorded as deferred) |

Completed or decided plans are retained for implementation history:

| Gap | State | Reference |
|---|---|---|
| **G1** | Done — native PE/Mach-O compare validation and non-blocking MSVC+PDB lane | [g1](g1-cross-platform-e2e.md) |
| **G2** | Done — build matrix folds into `compare`/`compare-release`; bundle soname-skew is wired | [g2](g2-build-config-and-bundle.md) |
| **G3** | Done — workflow scenarios and Markdown/HTML coverage | [g3](g3-workflow-examples-and-reporting.md) |
| **G5** | Done — `plugin-check` CLI and host↔plugin API | [g5](g5-plugin-bidirectional-contract.md) |
| **G6** | Done — BTF/CTF and SYCL PI/UR workflows | [g6](g6-kernel-btf-and-accelerator.md) |
| **G7** | Done — release recommendation | `abicheck/semver.py` |
| **G9** | Done — auditwheel/delocate vendored-library pairing, filename and embedded DT_SONAME/install-name both normalized via `strip_vendor_hash` | [g9](g9-wheel-vendored-matching.md) |
| **G10** | Done — manylinux glibc-floor / platform-baseline check (`platform_baseline_floor_raised`, declared via `--env-matrix`'s `runtime_floors`) | [g10](g10-glibc-floor-check.md) |
| **G16** | Done — header-scope toolchain diagnostics, `HeaderToolchainError`, and a real-host `integration` end-to-end check | [g16](g16-header-scope-toolchain-robustness.md) |
| **G8** | Decided — static/import archives are a by-design non-goal | [g8](g8-static-libraries.md) |
| **G12** | Done — security-hardening drift surface and policy preset | [g12](g12-security-hardening.md) |
| **G13** | Done — ELF snapshot captures `e_machine`/`EI_CLASS`/endianness; a mismatch is a dominating `BREAKING_KINDS` guard | [g13](g13-arch-mismatch-guard.md) |
| **G14** | Done — CPython extension recognition, `abi3`/Limited-API import-contract check, `scan --abi3` audit | [g14](g14-stable-abi-subset.md) |
| **G22** | Done — CLI consolidation & interface-contract enforcement ([ADR-037](../adr/037-cli-interface-contract.md)) | [g22](g22-cli-consolidation.md) |
| **G23** | Done — Python-level API diff for extension modules (`.pyi`/signature surface, 15 `python_api_*` ChangeKinds) | [g23](g23-python-level-api-diff.md) |

## How to pick up a plan

1. Read the plan and its registry entry/entries.
2. Implement against the **acceptance criteria** (each plan lists them).
3. Flip the registry `status` to `complete` (or a higher tier) and point
   `evidence` at the new tests/examples. The registry test will fail if you
   claim coverage without real evidence — that's the gate that proves the gap
   is actually closed.
4. Update the scorecard row in the evaluation doc.
