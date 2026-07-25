# Architecture Decision Records

## Status field convention

A documentation-lifecycle review (2026-07) found that a single "Status" word
per ADR routinely conflated three independent facts — whether the *decision*
was accepted, whether it's *implemented*, and whether that implementation
claim has been *verified* against the current code — which is exactly how
several ADRs went stale silently (e.g. ADR-022 said "implemented" when only
one of its four backends had shipped). Introducing a separate structured
frontmatter schema (`decision_status`/`implementation_status`/
`verification_status` fields, as some ADR tooling does) was considered and
deferred: every ADR here uses a single plain-prose `**Status:**` line, and
retrofitting 40+ files with fabricated metadata (owners, PR numbers, "last
verified" dates for documents nobody actually re-audited line-by-line) would
trade one inaccuracy for another. Instead, the convention **going forward**
is to keep encoding the same three facts in that one line, explicitly:

```
**Status:** <decision: Proposed | Accepted | Superseded by ADR-NNN | Deprecated>
— <implementation: implemented | partially implemented (name what's missing)
| not implemented>. <optional amendment note: what's stale, what superseded
it, where the current behavior is documented instead>
```

Good examples already in this table: ADR-022 ("partially implemented" +
naming exactly which backend shipped), ADR-037 (distinguishes "the contract
is implemented" from "enforcement is advisory until 1.0"), ADR-025 ("Proposed,
but substantially implemented/generalized elsewhere" + pointers to the ADRs
that absorbed it). When you touch an ADR and confirm a claim against current
code, update its `**Status:**` line rather than leaving the reader to infer
freshness from the file's git history. `scripts/check_usecase_docs_sync.py`
and the `adr-index-nav-sync` AI-readiness check keep the *registry* and *nav*
mechanically honest; the status line itself is still maintainer-verified
prose, not generated — treat a status claim you haven't personally checked
against the code as unverified, regardless of how confident it reads.

| # | Title | Status |
|---|-------|--------|
| [001](001-technology-stack.md) | Technology Stack — Python + pyelftools + castxml | Accepted — implemented, substantially amended |
| [002](002-multi-binary-release-compare.md) | Multi-binary / release compare UX and architecture | Accepted — implemented |
| [003](003-data-source-architecture.md) | Data Source Architecture — checks, instruments, and binary types (+ exploratory binary fingerprint extension) | Accepted — implemented; conceptually extended by the L0–L5 model (ADR-028–031, 041) |
| [004](004-report-filtering-and-deduplication.md) | Report Filtering, Deduplication, and Leaf-Change Mode | Accepted — implemented |
| [005](005-application-compat-check.md) | Application Compatibility Checking | Accepted — implemented |
| [006](006-package-level-comparison.md) | Package-Level Comparison | Accepted — implemented |
| [007](007-btf-ctf-debug-formats.md) | BTF and CTF Debug Format Support | Accepted — implemented |
| [008](008-full-stack-dependency-validation.md) | Full-Stack Dependency Validation | Accepted — implemented |
| [009](009-verdict-system-and-exit-codes.md) | Verdict System and Exit Code Contract | Accepted — implemented |
| [010](010-policy-profile-system.md) | Policy Profile System | Accepted — implemented |
| [011](011-change-classification-taxonomy.md) | ABI Change Classification Taxonomy | Accepted — implemented |
| [012](012-abicc-compatibility-layer.md) | ABICC Drop-In Compatibility Layer | Accepted — implemented |
| [013](013-suppression-system.md) | Suppression System Design | Accepted — implemented |
| [014](014-output-format-strategy.md) | Output Format Strategy | Accepted — implemented |
| [015](015-snapshot-serialization.md) | Snapshot Serialization and Schema Versioning | Accepted — implemented |
| [016](016-visibility-model.md) | Three-Tier Visibility Model | Accepted — implemented; extended by ADR-024's two-axis surface model |
| [017](017-github-action.md) | GitHub Action Design | Accepted — implemented |
| [018](018-cross-platform-support.md) | Cross-Platform Binary Format Support | Accepted — implemented |
| [019](019-testing-strategy.md) | Testing Strategy and Parity Validation | Accepted — implemented |
| [020a](020-build-context-capture.md) | Build-Context Aware Header Extraction | Accepted — implemented |
| [020b](020-sycl-and-heterogeneous-stack-support.md) | SYCL and Heterogeneous Computing Stack Support | Accepted — implemented |
| [021a](021-debug-artifact-resolution.md) | Debug Artifact Resolution Subsystem | Accepted — implemented |
| [021b](021-mcp-security-model.md) | MCP Security Model | Accepted — implemented |
| [022](022-baseline-registry.md) | Baseline Registry and Snapshot Distribution | Accepted — partially implemented (filesystem backend only) |
| [023](023-bundle-aware-multi-binary-analysis.md) | Bundle-Aware Multi-Binary ABI Analysis | Accepted — implemented |
| [024](024-public-abi-surface-resolution.md) | Public ABI Surface Resolution and False-Positive Traceability | Accepted — implemented |
| [025](025-pr-diff-source-evaluation.md) | PR-Diff-Aware ABI Evaluation (Source Diff as Trigger and Localizer) | Proposed; D1–D3 absorbed by ADR-033/035, D4 still future work |
| [026](026-source-only-undetectable-changes.md) | Source-Only Changes and the Evidence-Tier Boundary | Accepted — substantially superseded by ADR-028/030/035/038 (its "no embedded Clang" conclusion was reversed) |
| [027](027-api-surface-intelligence.md) | API Surface Intelligence — Structure Metrics, Idiom Detection, Cross-Library Reasoning, Pattern-Aware Verdicts | Accepted |
| [028](028-source-build-evidence-pack.md) | Optional Source and Build Evidence Pack Architecture | Accepted — implemented |
| [029](029-build-graph-toolchain-context-capture.md) | Build Graph and Toolchain Context Capture | Accepted — implemented |
| [030](030-source-abi-replay-and-linked-source-surface.md) | Source ABI Replay and Linked Source Surface | Accepted — implemented |
| [031](031-source-implementation-graph-augmentation.md) | Source and Implementation Graph Augmentation | Accepted — implemented |
| [032](032-evidence-extractor-plugin-interface.md) | Evidence Extractor Plugin Interface and Security Model | Accepted — implemented |
| [033](033-ci-rollout-performance-and-validation.md) | CI Rollout, Performance, Caching, and Validation Strategy | Accepted — implemented |
| [034](034-managed-runtime-and-non-c-abi-frontends.md) | Managed-Runtime and Non-C ABI Frontends | Proposed |
| [035](035-pr-tier-source-intelligence-and-crosscheck.md) | PR-Tier Source Intelligence and Cross-Source Validation | Accepted — implemented (G19, D1–D10) |
| [036](036-report-view-model.md) | Report view-model and canonical report severity | Accepted |
| [037](037-cli-interface-contract.md) | CLI Interface Contract, Configuration Balance, and Extension Policy | Accepted — implemented (G22) |
| [038](038-build-integrated-fact-collection-variants.md) | Working With Sources — Full-Scan and Two Build-Injection Flows | Accepted — implemented |
| [039](039-build-context-reconciliation.md) | Build-Context Reconciliation of Context-Free Header-Parse Artifacts | Accepted — implemented |
| [040](040-compare-surface-reduction.md) | `compare` Surface Reduction — Side-Aware Flags, Config Demotion, Run Profiles | Accepted — phased (Phase A landed) |
| [041](041-compiler-facts-semantic-impact-graph.md) | Compiler-Facts Semantic Impact Graph — Roadmap and P0 Slice | Accepted — P0 slice 1 implemented |
| [042](042-compatibility-and-gate-decision-separation.md) | Formal separation of CompatibilityDecision and GateDecision | Accepted — implemented for JSON/SARIF/compare-release gate summaries |
| [043](043-cli-pre-1.0-surface-reset.md) | Pre-1.0 CLI Surface Reset — Root Command Collapse, Depth Ladder Narrowing, and Dry-Run Unification | Accepted — implemented |
| [044](044-reachability-aware-suppression.md) | Reachability-Aware Suppression and the Effective Public ABI | Accepted — P0 slice implemented |
| [045](045-identity-based-old-new-entity-matching.md) | Identity-Based Old/New Entity Matching | Accepted — implemented for `RecordType` and `EnumType` |
| [046](046-source-graph-identity-v2-and-evidence-merge.md) | Source Graph Identity v2 — USR-Based Entity Resolution and Evidence-Preserving Merge | Accepted — D1 (`relation_key` half), D2 (evidence-preserving merge), D3 (per-role coverage matrix), D5 (partial, `TraversalPolicy` for the call-graph walk), D6 (partial, two-tier proof-path preference) implemented; D1's `occurrence_id` half open; D4 deliberately deferred (ADR-048 covers its practical value at smaller scope) |
| [047](047-github-actions-integration-model.md) | GitHub Actions Integration Model — Project Lifecycle Over Aggregate-Centric Design | Proposed — not implemented; see [G30](../plans/g30-github-actions-integration-model.md) |
| [048](048-canonical-entity-identity-and-graph-reconciliation.md) | Canonical Entity Identity and Graph Reconciliation (G31 Phase B) | Accepted — implemented |
| [049](049-contract-relevance-and-compatibility-configuration.md) | Contract Relevance and Compatibility Configuration | Proposed — not implemented |
| [050](050-comparability-contract-and-multi-tu-manifest.md) | Comparability Contract — Profile/Scope Fingerprints and the Multi-TU Manifest | Proposed — not implemented; see [G32](../plans/g32-comparability-contract-and-multi-tu-manifest.md) |
| [051](051-documentation-operational-model.md) | Documentation Operational Model (Ownership Registry + Docs-Contract Gate) | Accepted — partially implemented (Stage 1 done; Stages 2-5 deferred) |
| [052](052-unified-impact-assessment-model.md) | Unified Impact Assessment Model (G29 Phase 3, slices 1-5) | Accepted — slices 1-5 implemented |
