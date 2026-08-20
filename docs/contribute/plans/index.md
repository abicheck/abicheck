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
| **CLI cleanup, phase two** | [Reviewed plan: what phase one left behind](cli-cleanup-phase-two.md) | [ADR-037](../adr/037-cli-interface-contract.md), [ADR-043](../adr/043-cli-pre-1.0-surface-reset.md), [ADR-047](../adr/047-github-actions-integration-model.md), [ADR-054](../adr/054-cli-project-integration-surface-consolidation.md), [ADR-056](../adr/056-multi-artifact-library-set-scan.md) · In progress; L (seven independently sequenced PRs — **PR 0/1/2 implemented**: green-CI prerequisite; presentation removals; aggregate policy schema — remaining: a separate, later PR 1b moving annotations to the Action once a release-report persistence prerequisite lands; build execution into trusted config; gate-semantics ADR; `--artifact-set` syntax refinement) |
| **Duplication & convergence assessment** | [Project-wide duplication assessment and convergence plan](duplication-and-convergence-assessment.md) | [ADR-037](../adr/037-cli-interface-contract.md), [ADR-049](../adr/049-contract-relevance-and-compatibility-configuration.md), [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md), [ADR-054](../adr/054-cli-project-integration-surface-consolidation.md), [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md), [ADR-056](../adr/056-multi-artifact-library-set-scan.md) · Proposed; not started; XL (phased: Phase 0 guardrail tests, Phase 1 artifact-resolution convergence, Phase 2 effective-configuration contract, Phase 3 `ExitDecision` completion, Phase 4 canonical report envelope, Phase 5 compat/multi-artifact migration — generalizes CLI-cleanup-phase-two's PR B/C/G1 across every operation) |
| **Public contract default** | [Implementation and rollout](public-contract-default.md) | [ADR-049](../adr/049-contract-relevance-and-compatibility-configuration.md) · Accepted; implementation in progress (see the plan's "Work breakdown" section for current per-phase status) · L/XL (effective config, evidence completeness, L0 reconciliation, report/snapshot migration) |
| **G19** | [PR-tier source intelligence & cross-source validation](g19-pr-source-intelligence.md) | [ADR-035](../adr/035-pr-tier-source-intelligence-and-crosscheck.md) · XL (phased) |
| **G24** | [Linux ABI/API detection gap closure](g24-linux-abi-gap-closure.md) | — · L (phased: ELF facts → vtable machinery → clang flag extraction → kABI/ecosystem; macOS/Windows gaps recorded as deferred) |
| **G28** | [CastXML/Clang L2 parity: hardening & remaining phases](g28-castxml-clang-l2-parity-hardening.md) | [ADR-001](../adr/001-technology-stack.md), [ADR-003](../adr/003-data-source-architecture.md) D8/D9, [ADR-037](../adr/037-cli-interface-contract.md) D8 · Phase 0–4 done; Phase 5 M (overlaps [G4](g4-header-ast-extractor.md)) |
| **G29** | [Impact-analysis layer: unified graph-driven impact model](g29-impact-analysis-layer.md) | [ADR-044](../adr/044-reachability-aware-suppression.md), [ADR-031](../adr/031-source-implementation-graph-augmentation.md), [ADR-046](../adr/046-source-graph-identity-v2-and-evidence-merge.md) · XL (phased: Phase 1 done — tri-state reachability, [PR #607](https://github.com/abicheck/abicheck/pull/607); Phase 2 accepted and implemented, D1-D6 (D4 scoped) — [ADR-046](../adr/046-source-graph-identity-v2-and-evidence-merge.md); Phase 3 slices 1-9 implemented — [ADR-052](../adr/052-unified-impact-assessment-model.md); Phase 4 slice 1 — consumer graph + the consumer/source join, closing ADR-046 D6's tier 1 — implemented, [ADR-057](../adr/057-consumer-graph-and-impact-join.md), with the use-case-manifest and runtime-trace halves still open; Phases 5–6 open) |
| **G30** | [GitHub Actions integration model: project lifecycle backlog](g30-github-actions-integration-model.md) | [ADR-047](../adr/047-github-actions-integration-model.md) · XL (phased: P0 done; main P1 lifecycle done, including P1.7's scenario-first documentation IA; P2 not started except its first slice, TU→link-unit→DSO attribution core — [ADR-053](../adr/053-tu-link-unit-dso-attribution.md) — with pipeline wiring still open) |
| **G31** | [Header-graph default-on: follow-up phases B–D](g31-header-graph-default-on-followup.md) — independent of G29 above; drafted as "G29" before that letter was found taken, see its own naming note | [ADR-041](../adr/041-compiler-facts-semantic-impact-graph.md) · Phase A done (header-graph/header-graph-includes flipped default-on); Phase B done — canonical entity identity/graph reconciliation, see [ADR-048](../adr/048-canonical-entity-identity-and-graph-reconciliation.md); Phases C–D open |
| **G32** | [Comparability contract: profile/scope fingerprints and the multi-TU manifest](g32-comparability-contract-and-multi-tu-manifest.md) | [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md) · XL (phased: Phase 0 and Phases A–E all done, including the post-merge D5/D6 review follow-up) |
| **G33** | [Typed API convergence: schema registry, Request/Result completeness, MCP dedup](g33-typed-api-and-mcp-convergence.md) | [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md) · Accepted — implemented; L/XL (phased: Phases 0–5 all done — schema registry, `CompareRequest`/`CompareResult` completeness including the CLI's own migration onto the shared resolution, `abi_compare`'s rewrite, and Phase 5's typed `DumpRequest` + `abi_dump`/`abi_scan` parity; Phase 6 is a standing sequencing constraint on ADR-049's rollout, not work this plan implements. One follow-up left open and named: the native `dump` CLI does not yet build a `DumpRequest`) |
| **G34** | [Producer/consumer compiler-profile separation and compiler-matrix hardening](g34-producer-consumer-compiler-profile-separation.md) | — · XL (phased: Phase 0 schema split — `profiles.<id>.consumer_compile` schema + `run-plan.json` projection done, L2/L4 extraction+merge integration still open; Phase A toolchain-identity enforcement — `project validate --toolchain-bindings` probes a resolved binding's real compiler family/version against the declared constraint done (MSVC skipped, documented limitation), a dump/compare-time hard-fail before extraction still open pending a binding-resolution call path that doesn't exist yet; Phase B per-profile AST frontend — schema + `run-plan.json` projection + real `--ast-frontend` wiring done (`check-project.yml` forwards `matrix.compile_ast_frontend` per cell), `consumer_compile.frontend` deliberately unforwarded until Phase 0's second extraction pass exists, and an end-to-end GCC-castxml/DPC++-clang fixture still open (G17); Phase D per-finding cross-profile reconciliation — done, `aggregate`'s `finding_matrix` block (schema 1.2) reconciles one logical finding across profiles with affected/unaffected/undetermined lists; Phase C Actions-matrix native-OS scheduling + per-cell dependency-source — done, `runs_on`/`dependency_source` are resolved per profile and drive the check cell's `runs-on:` and dependency provisioning) |
| **G35** | [Multi-artifact / library-set `scan`](g35-multi-artifact-scan.md) | [ADR-056](../adr/056-multi-artifact-library-set-scan.md) · Proposed; M (phased — Phases 1-4's core engine/detector/CLI/Action slice shipped ahead of formal sign-off; see the plan's "Implementation status" note for what remains deferred) |
| **G36** | [Native compatibility agent skills: design, build, publish](g36-native-compatibility-agent-skills.md) | [ADR-058](../adr/058-native-compatibility-agent-skills.md) · Accepted; not implemented; L/XL (phased: P0 architecture/first-release, P1 reliability/distribution, P2 portfolio expansion contingent on admission criteria) |
| **G37** | [Agent skill quality evaluation: measuring whether the skills work](g37-agent-skill-quality-evaluation.md) | [ADR-058](../adr/058-native-compatibility-agent-skills.md) · Proposed; Phase 0 implemented (contracts, the generated eval pack, and the two deterministic `pr` gates — `skill-eval-pack`/`skill-eval-freshness`); L (phased: Phase 0–1 contracts + deterministic grading core in `pr`, Phase 2 off-CI live runner plus the deterministic evidence/freshness gate — no model runs in CI, Phases 3–4 corpus and cross-agent, Phase 5 comparative lift in `agent-benchmark`, Phase 6 publication gate). Supersedes [G36](g36-native-compatibility-agent-skills.md)'s P1.1/P1.4/P1.5 implementation detail |

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
