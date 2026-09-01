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

### The `**Verified:**` receipt (opt-in)

A 2026-08 status review found the residual half of the same problem, which no
amount of status *prose* solves: ADR-049's Status said its shadow evaluator
"is not called from any pipeline stage" and that nothing was wired into the
CLI or reports, and five merged PRs later that was simply false. The status
line was internally consistent, agreed with the index row, and was wrong —
because the code moved and nobody re-read it. Comparing two documents cannot
catch that; only re-reading the code can.

So a second, **optional** metadata line records when someone last did:

```text
**Verified:** <ref>@<sha> on <YYYY-MM-DD>
```

placed directly after the Status paragraph. It means "a maintainer checked
this ADR's Status claims against the tree at that commit" — nothing more.

**Name a commit on the default branch, not the branch you're writing the
receipt on.** A branch commit stops existing once the PR squash-merges, and
because the `ai-readiness` job checks out full history, an unresolvable sha
is an error rather than a tolerated skip — so a receipt anchored to a PR
commit passes on that PR and then fails on `main` permanently. Since a PR
that adds a receipt normally doesn't change the code being attested, the
right sha is the `main` commit the branch is based on. `adr-status-sync`
enforces this. It
is opt-in precisely so it stays truthful: an ADR without one has simply never
been checked, which is the honest state of most of the files below and is not
an error. Adding one you didn't actually perform is worse than having none.

**Name a module in full if you want it watched** — `abicheck/foo.py`, not
`_foo.py` after a sibling. Family shorthand is deliberately not guessed at:
the gate would have to infer whether the family is replaced or appended, and
a wrong inference either watches an unrelated file or silently watches
nothing. A full path is exact, auditable from the Status line alone, and
keeps working after the file is renamed or deleted (which is when a claim is
most likely to have gone stale).

What it buys is a tripwire rather than a promise. `adr-status-sync`
(implemented in `scripts/adr_status_sync.py`, registered as a check by
`scripts/check_ai_readiness.py`) reads the first-party module paths the Status
paragraph *names* and warns when commits after the recorded sha have touched
any of them — i.e. when the code a claim describes has moved out from under
it. That's a WARN, not an ERROR: a changed module doesn't prove the claim went
stale, it proves nobody has re-read it since. Move the line forward when you
re-check, or correct the claim.

The same check ERRORs on a flat *contradiction* between an ADR's own Status
and its row in the table below — one side claiming nothing is implemented
while the other claims something is (which is exactly how ADR-056's row went
stale). It deliberately does **not** require the two to paraphrase each other:
the index cell is an abridgement of a paragraph that often runs a dozen lines,
and an earlier prototype that compared them more strictly flagged 15 of 56
ADRs, nearly all false positives.

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
| [021b](021-mcp-security-model.md) | MCP Security Model | Deprecated — Retired: MCP interface removed |
| [022](022-baseline-registry.md) | Baseline Registry and Snapshot Distribution | Accepted — not implemented; the filesystem backend and `baseline` command group that once shipped were deleted by [ADR-043](043-cli-pre-1.0-surface-reset.md) D4, which also records recreating a registry as a non-goal |
| [023](023-bundle-aware-multi-binary-analysis.md) | Bundle-Aware Multi-Binary ABI Analysis | Accepted — implemented |
| [024](024-public-abi-surface-resolution.md) | Public ABI Surface Resolution and False-Positive Traceability | Accepted — implemented |
| [025](025-pr-diff-source-evaluation.md) | PR-Diff-Aware ABI Evaluation (Source Diff as Trigger and Localizer) | Proposed; D1–D3 absorbed by ADR-033/035, D4 still future work |
| [026](026-source-only-undetectable-changes.md) | Source-Only Changes and the Evidence-Tier Boundary | Accepted — substantially superseded by ADR-028/030/035/038 (its "no embedded Clang" conclusion was reversed) |
| [027](027-api-surface-intelligence.md) | API Surface Intelligence — Structure Metrics, Idiom Detection, Cross-Library Reasoning, Pattern-Aware Verdicts | Accepted — Phases 0-5 implemented; `--pattern-verdicts` default-on flip deferred pending release-cycle FP-rate/parity validation |
| [028](028-source-build-evidence-pack.md) | Optional Source and Build Evidence Pack Architecture | Accepted — implemented |
| [029](029-build-graph-toolchain-context-capture.md) | Build Graph and Toolchain Context Capture | Accepted — implemented |
| [030](030-source-abi-replay-and-linked-source-surface.md) | Source ABI Replay and Linked Source Surface | Accepted — implemented |
| [031](031-source-implementation-graph-augmentation.md) | Source and Implementation Graph Augmentation | Accepted — implemented |
| [032](032-evidence-extractor-plugin-interface.md) | Evidence Extractor Plugin Interface and Security Model | Accepted — implemented |
| [033](033-ci-rollout-performance-and-validation.md) | CI Rollout, Performance, Caching, and Validation Strategy | Accepted — implemented |
| [034](034-managed-runtime-and-non-c-abi-frontends.md) | Managed-Runtime and Non-C ABI Frontends | Proposed |
| [035](035-pr-tier-source-intelligence-and-crosscheck.md) | PR-Tier Source Intelligence and Cross-Source Validation | Accepted — implemented (G19, D1–D10) |
| [036](036-report-view-model.md) | Report view-model and canonical report severity | Accepted — core implemented (Increments 1-2); Increment 3 (routing `html_report.py`/`pr_comment.py` through `ReportModel`) remains optional cleanup |
| [037](037-cli-interface-contract.md) | CLI Interface Contract, Configuration Balance, and Extension Policy | Accepted — implemented (G22) |
| [038](038-build-integrated-fact-collection-variants.md) | Working With Sources — Full-Scan and Two Build-Injection Flows | Accepted — implemented |
| [039](039-build-context-reconciliation.md) | Build-Context Reconciliation of Context-Free Header-Parse Artifacts | Accepted — implemented |
| [040](040-compare-surface-reduction.md) | `compare` Surface Reduction — Side-Aware Flags, Config Demotion, Run Profiles | Accepted — phased implementation substantially complete (Phase A + Phase B landed, Phase C Lever-1 landed except the `ast-frontend` carve-out, Phase D landed as a constraint-aware subset) |
| [041](041-compiler-facts-semantic-impact-graph.md) | Compiler-Facts Semantic Impact Graph — Roadmap and P0 Slice | Accepted — P0 slices 1-4, the header-only-graph addendum, and P1 items 1-5 implemented; remainder is roadmap, not a shipping commitment |
| [042](042-compatibility-and-gate-decision-separation.md) | Formal separation of CompatibilityDecision and GateDecision | Accepted — implemented for JSON/SARIF/`compare-release` gate summaries and `html_report.py`'s CI Gate card; `mcp_server.py`/`junit_report.py` still compute an exit code inline in places |
| [043](043-cli-pre-1.0-surface-reset.md) | Pre-1.0 CLI Surface Reset — Root Command Collapse, Depth Ladder Narrowing, and Dry-Run Unification | Accepted — implemented |
| [044](044-reachability-aware-suppression.md) | Reachability-Aware Suppression and the Effective Public ABI | Accepted — P0, P1, and P2 all implemented (see ADR for exact scope) |
| [045](045-identity-based-old-new-entity-matching.md) | Identity-Based Old/New Entity Matching | Accepted — implemented for `RecordType` and `EnumType` |
| [046](046-source-graph-identity-v2-and-evidence-merge.md) | Source Graph Identity v2 — USR-Based Entity Resolution and Evidence-Preserving Merge | Accepted — D1-D6 all implemented, each to the documented scope (D4 is a deliberately scoped subset of the originally sketched full rewrite; see ADR for exactly what's covered per decision) |
| [047](047-github-actions-integration-model.md) | GitHub Actions Integration Model — Project Lifecycle Over Aggregate-Centric Design | Accepted — substantially implemented (P0 and the main P1 lifecycle implemented; P2 partially implemented, see ADR); see [G30](../plans/g30-github-actions-integration-model.md) |
| [048](048-canonical-entity-identity-and-graph-reconciliation.md) | Canonical Entity Identity and Graph Reconciliation (G31 Phase B) | Accepted — implemented |
| [049](049-contract-relevance-and-compatibility-configuration.md) | Contract Relevance and Compatibility Configuration | Accepted (2026-07-26) — Phases 0-6 implemented (vocabulary, typed config/resolver/packs, finding identity, shadow evaluator in all three contract domains, persisted evidence context + replay, one resolved config per front end, `--contract` domain selection); Phase 7 partially (coverage exit landed; authoritative decision, default flip, and `aggregate` folding still open) |
| [050](050-comparability-contract-and-multi-tu-manifest.md) | Comparability Contract — Profile/Scope Fingerprints and the Multi-TU Manifest | Accepted — implemented (Phase 0 and Phases A-E; D1-D6); see [G32](../plans/g32-comparability-contract-and-multi-tu-manifest.md) |
| [051](051-documentation-operational-model.md) | Documentation Operational Model (Ownership Registry + Docs-Contract Gate) | Accepted — Stages 1-4 implemented; Stage 5 explicitly deferred |
| [052](052-unified-impact-assessment-model.md) | Unified Impact Assessment Model (G29 Phase 3, slices 1-11) | Accepted — slices 1-11 implemented |
| [053](053-tu-link-unit-dso-attribution.md) | TU → Link-Unit → DSO Source-Evidence Attribution | Accepted — implemented (core algorithm + validator; CLI/Action pipeline wiring deferred, see D5) |
| [054](054-cli-project-integration-surface-consolidation.md) | CLI Project-Integration Surface Consolidation | Accepted — implemented |
| [055](055-typed-request-result-completeness-and-schema-registry.md) | Typed Request/Result Completeness and a Schema-Version Registry | Accepted — implemented (D1-D4), including D1's structural half: the CLI and typed API share one input resolution. D4 (MCP dedup) and other MCP-specific claims are historical — the MCP server was later removed |
| [056](056-multi-artifact-library-set-scan.md) | Multi-Artifact / Library-Set `scan` | Proposed — partially implemented (Phases 1-4's engine/detector/CLI/Action slice shipped ahead of formal sign-off; MCP half, example catalog, and `--dry-run` estimator deferred); see [G35](../plans/g35-multi-artifact-scan.md) |
| [057](057-consumer-graph-and-impact-join.md) | Consumer Graph and the Consumer/Source Impact Join (G29 Phase 4, slice 1) | Accepted — slice 1 implemented (consumer graph, the join, ADR-046 D6's tier-1 selector, the `--used-by` overlay wiring); the use-case manifest and runtime-trace halves of Phase 4 are not implemented; see [G29](../plans/g29-impact-analysis-layer.md) |
| [058](058-native-compatibility-agent-skills.md) | Native Compatibility Agent Skills — User-Task-First Domain Layer | Accepted — partially implemented (G36 P0.1–P0.3/P0.6–P0.9: the generator and gates shipped; P0.4/P0.5 product-surface items and all of P1 remain). Portfolio reset to one skill (2026-08-20 amendment, superseding the 2026-08-11 four-skill freeze): `review-native-library-change` (renamed from `native-binary-compatibility-review`) is the sole published skill, an unvalidated internal candidate; the other three are removed from `skills-src/` and every generated tree, recoverable from git history. A second, same-date amendment ("PR 2") then rewrote that skill's workflow content — customer-outcome framing, a ten-step decision procedure, an integrated named-consumer branch, a narrowed v0.1 validated scope, a structured decision-report contract — still unvalidated. A third, same-date amendment ("PR 3.5") renamed it again to `check-abi-compatibility` (a user-outcome name rather than a mechanism name) and recorded, as design intent only, that PR 4's external-distribution deliverable should be an npm/npx-installable package published from this repository rather than a separate distribution repo. A fourth, same-date amendment ("PR 3") landed the full G37 evaluation corpus (12 scenarios) and a real 48-run pilot under the new name; its dominant finding is a harness confound (a 12-turn budget cutting off 31% of runs, asymmetrically by arm) rather than a skill-quality result — see `agent-evals/skills/pilot-results/README.md`. A fifth amendment ("Harbor task battery") added a generated [Harbor](https://www.harborframework.com) task directory per scenario (`agent-evals/skills/harbor/tasks/`) — schema-validated against the real `harbor` package and end-to-end verified for Category A reference solutions, but never run through an actual Harbor trial (no working container/sandbox runtime in this environment). A sixth, same-day amendment ("Harbor made canonical") then decided Harbor is the surface for all new scenario/trial work going forward; `runners/claude_code.py` is kept only as the historical record of the existing pilot — see `agent-evals/skills/harbor/CLAUDE.md`. Still unvalidated; see [G36](../plans/g36-native-compatibility-agent-skills.md) / [G37](../plans/g37-agent-skill-quality-evaluation.md) |
| [059](059-compressed-snapshot-storage.md) | Compressed Snapshot Storage Envelope | Accepted — implemented (core snapshot I/O, `dump`, `compare`/`scan --against`/Python API, the snapshot cache, `actions/baseline`, the root Action's `dump`-mode `snapshot-compression` input, compressed-release-asset auto-fetch, and both publish workflows' `snapshot-compression` input; `resolve-baseline` needed no changes at all — already transparent); baseline-set manifest v2, a deterministic `.tar.zst` packager, and the wider docs sweep deferred, see the ADR's own "What this ADR does not (yet) close" |
| [060](060-synthetic-consumer-compile-probe-deferral.md) | Synthetic-Consumer Compile-Probe Layer — Deferred | Accepted — not implemented (decision to defer), and no follow-up phase is scheduled; see [G31](../plans/g31-header-graph-default-on-followup.md) Phase D |
| [061](061-responsibility-package-architecture.md) | Responsibility-Package Architecture and Flat-Namespace Migration | Accepted — Phases 0-1 implemented (architecture enforcement plus the aggregation workflow migration and compatibility facade); Phases 2-4 in progress; **Phase 5 is fully closed — all four items done**: the `model` package and the metadata dataclass/parser split landed, the CastXML/Clang parser split is done on both backends, D9's change-catalog work is done (all 4 registry-validation properties enforced, 397 entries repartitioned into `model/change_catalog/{symbols,types,platform,build,source}.py` by taxonomy), the `IMPORT_CYCLE_ALLOWLIST` audit/stale-edge cleanup is done, and source-graph values/construction/comparison separation's internal-caller migration off the `buildsource/source_graph.py` facade is closed for every caller (the shared node/edge-classification predicates relocated into `model/source_graph_query.py`; `template_graph.py` closed last via a split into `template_graph_fold.py`) — D8 is now literally satisfied; see the ADR's own item 2 paragraph for the full closure account. Phase 4 also made progress: `policy_file.py` is now classified `policy` via a structural `PolicyFileProtocol`/`ReclassifyRuleProtocol` pair, thinning `service.py` further (451 → 283 lines) |
| [062](062-project-snapshot-storage-v2.md) | Project Snapshot Storage v2 — Content-Addressed Sections, Explicit Fact Availability, and Occurrence-Preserving Identity | Proposed — partially implemented; Phase 0 primitives implemented (`abicheck/storage/`: fact availability, occurrence-preserving identity, canonical encoding, separated version axes); Phase 1's A1.1 object model (`PackageManifest`/`VariantRef`/`ArtifactRef`/`ObjectRef`/`ObjectStore` in `abicheck/storage/package.py`) also implemented, with no directory-backed store or writer yet; nothing wired to a producer or reader yet so every existing snapshot/baseline/`BundleFacts` document is unchanged; the rest of Phase 1 and all of Phase 2 not implemented; see [storage format v2 plan](../plans/storage-format-v2.md) |
| [063](063-one-semantic-pipeline.md) | One Semantic Pipeline — Unifying Application, Fact, Identity, and Outcome Models | Proposed — roadmap ADR. Phase 0 (`Fact[T]`/`FactStatus` infrastructure, plus every known reader migrated off the legacy fields onto it) is complete; Phase 1's `dump`/`scan` typed-API convergence landed for ELF (PE/Mach-O still open); Phase 2's `EntityId`/`ScopePath` primitive, typed scope tracking, the `entity_id` carrier, and its wire-schema-v2 persistence have landed (no consumer reads it yet); Phase 3's public-surface-as-graph-query plumbing has landed (`compare/surface_graph.py`, `policy/public_surface.py`, `AbiSnapshot.surface_graph`, threaded `EntityId` resolution through `compare()`/`compare_snapshots()`) but `surface.py`/`export_surface.py`'s own traversal algorithms stay in place rather than being deleted, and the new graph builder's node ids don't yet unify with the pre-existing L5 builder's; Phases 4-10 not yet. Generalizes and finishes ADR-042/046/048/049/050/055/061/062 rather than replacing any of them; see [implementation plan](../plans/one-semantic-pipeline.md) |
| [064](064-canonical-gate-algorithm-and-exit-decision.md) | One Canonical Gate Algorithm and Exit-Decision Precedence | Accepted — partially implemented. Formalizes CLI cleanup phase two's PR G2: removing `--exit-code-scheme`, keeping `auto`'s inference as the only behaviour, and the full six-axis `ExitDecision` precedence (evidence-contract error, budget overflow, not-comparable, mode-dependent removed-required-library rank, gate, coverage/assurance floors). PR G1's three-axis core (compatibility gate, contract coverage, analysis assurance) shipped additively before this ADR (#789). Of this ADR's own two-stage plan, stage 1a landed complete: `resolve_scan_exit_decision`/`resolve_release_exit_decision` (`abicheck/policy/exit_decision_precedence.py`), pure functions reproducing the remaining axes' precedence (including a release's independent operational-error axis), unit-tested against the real code they model. Stage 1b landed partially: `ExitDecision.to_dict` serializes all five ADR-064 fields (report schema 2.47/1.22), `scan`'s `NOT_COMPARABLE` outcome and the release fan-out's JSON summary both persist a real `exit` block now, verified to always agree numerically with the real, untouched exit-code functions they parallel. `scan`'s `_BudgetOverflow`/`_EvidenceContractError` abort points now persist a decision for both the typed `ScanResult` API (`abicheck.workflows.scan_abort_result.scan_abort_result_fields`, `SCAN_SCHEMA_VERSION` 1.23) and the native `scan --format json` CLI path (`cli_scan._emit_scan_abort_report`, a `ScanOutcome`-envelope-compatible payload); a *late* `_BudgetOverflow` (after a real gate/coverage/assurance/audit decision already exists) also preserves those prior contributions instead of discarding them (`attach_prior_on_budget_overflow`, covering both the baseline-compare and audit-only branches). Still open: the release fan-out's `GateOptions` unification, a full cross-front-end parity pass, and stage 2 (the atomic `--exit-code-scheme` removal itself); see [cli-cleanup-phase-two.md](../plans/cli-cleanup-phase-two.md) for the authoritative status |
