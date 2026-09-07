---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Product-gap audit and first vertical slice (2026-09-07)

**Owner ADRs:** [ADR-061](../adr/061-responsibility-package-architecture.md)
(routing), [ADR-068](../adr/068-one-comparison-product-and-scan-retirement.md)
(scan retirement, scope/operand parity). This document does not introduce a
new ADR — every gap below already has an owning ADR or plan; see the table
in §0.

**Status:** One real, honest, reviewable vertical slice landed (§3's
`analysis.assurance` truthfulness gate — both halves, rejection *and*
execution). Sections 4-9 received audit/tracking treatment only, per this
task's explicit scope limit — no code changes for those sections in this
pass.

**Verified against:** `main` at `f7b4fdcc7` (this branch's fork point,
2026-09-07), plus a fresh clone of `abicheck/integration-lab` at
`b50d35773a6b7f020e5c8c57e2e567cccbb13d94`.

---

## 0. How this maps to existing ownership

Per root `AGENTS.md`'s task-routing table and each ADR's own
cross-references, every one of the task's 9 sections already has an owner.
Nothing here duplicates that ownership — each subsection below either adds a
dated audit note to the real owning document, or (§3) implements against it.

| § | Task's own heading | Owner ADR | Owner plan(s) |
|---|---|---|---|
| 1 | Rebase the work on current truth | — (process) | this document |
| 2 | Non-negotiable semantics | `vision.md`, ADR-028/049/050/063/064/065/066/067 | `vision-api-abi-evolution.md` |
| 3 | Declared project checks executable and truthful | — | [`g41-baseline-consumer-context-and-declarative-assurance.md`](g41-baseline-consumer-context-and-declarative-assurance.md) Phase 3, [`g42-check-identity-environments-and-provider-resolution.md`](g42-check-identity-environments-and-provider-resolution.md) |
| 4 | Converge analysis capabilities before retiring scan | [ADR-068](../adr/068-one-comparison-product-and-scan-retirement.md) | [`one-comparison-product.md`](one-comparison-product.md) |
| 5 | Scope and operand parity | [ADR-065](../adr/065-comparison-scope-selection-and-completeness.md), ADR-068 D2 | `one-comparison-product.md` §2, `vision-api-abi-evolution.md` |
| 6 | Scalable evidence collection and reuse | — | [`g39-per-finding-evidence-provider-model.md`](g39-per-finding-evidence-provider-model.md), `abicheck/buildsource/build_cache.py`/`source_replay.py`'s own module docs |
| 7 | Governance, environments, consumers | — | `g42-…`'s "Named environments" section, [`g34-producer-consumer-compiler-profile-separation.md`](g34-producer-consumer-compiler-profile-separation.md), `vision-api-abi-evolution.md`'s consumer-specification workstream (D) |
| 8 | Header-only, Python, reporting follow-ups | [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md) (typed API/schema) | [`g45-header-only-targets-and-build-output-emit-helper.md`](g45-header-only-targets-and-build-output-emit-helper.md), `vision-api-abi-evolution.md` §F (header-only comparison) |
| 9 | Delivery discipline, cross-repo handoff | — (process) | `one-comparison-product.md` §6 (PR sequence), root `AGENTS.md`'s bug-class/decision-making principles |

## 1. Rebase on current truth — what was actually checked

- Fetched `origin/main`; the target branch `claude/abicheck-product-gaps-adr-7amyix`
  was an unmerged-nothing fork of `main` (no divergent commits) — safe to
  build on directly.
- The stale `p0-*`/`ops/*` branches visible in `origin` (`p0-2-bazel-root-targets`,
  `p0-3-*`, `p0-4-analysis-assurance`, `p0-4-followup-fixes*`, …) share **no
  common ancestor** with current `main` (`git merge-base` fails for all of
  them) — an unrelated, disjoint history, not in-flight work on the current
  codebase. Ignored, not treated as prior art.
- Open PRs in `abicheck/abicheck` at audit time: **one** — [#1125](https://github.com/abicheck/abicheck/pull/1125)
  ("add `FindingEvolution` state and migrate `private_header_leak` onto
  it"), implementing ADR-068 Phase 1 item 2's second axis. It does not touch
  `CheckSpec`/`RunPlanCheck`/`derive_baseline_libraries`, so there is no
  overlap with this pass's vertical slice; it's real, in-flight progress on
  §4/§5 and is referenced from `one-comparison-product.md` directly rather
  than restated here.
- `abicheck/integration-lab` **was** reachable this session (cloned via
  `add_repo`, `b50d35773a6b7f020e5c8c57e2e567cccbb13d94`) — see §"Integration-lab
  handoff" below for what was actually checked there. Its `.abicheck.yml`
  declares no `checks[].analysis:` block at all today, so nothing there is
  affected by an unsupported-value rejection landing.
- Re-verified, not assumed: which capabilities the task's "do not
  reimplement" list names are actually landed.
  - **Declared target-pack routing, candidate consumer-context extraction**
    (G34 Phase 0) — landed; `RunPlanCheck.consumer_compile_*` fields exist
    and are forwarded by `check-project.yml`.
  - **Ordinary typed binary dump execution** — landed for ELF via
    `execute_dump_request`; PE/Mach-O still route through the legacy
    `handle_non_elf_dump` path (see `AGENTS.md`'s "PR C" known-gap entry,
    unchanged by this pass).
  - **Release selection/acquisition/completeness slices** (ADR-065) — landed
    per that ADR's own Status line ("S1, S2, S3 and S4 implemented").
  - **Disposition audit** (ADR-067) — landed for slices S1/S2 per that ADR's
    Status line; S3-S4 open, unchanged here.
  - **Basic offline longitudinal history** (ADR-066) — **not** implemented;
    ADR-066's own Status line says so plainly ("Proposed — not implemented").
    The task's phrasing ("do not reimplement… basic offline longitudinal
    history") is inaccurate against current `main` — flagged here rather
    than silently accepted, per this document's own "verify against real
    code before writing a gap" instruction. `project history` (`cli_project.py`)
    exists as a command but is a thinner offline diff-of-snapshots utility,
    not ADR-066's versioning-policy model.
  - **Basic header-only dump/snapshot comparison** — largely landed
    (`header_graph.py`'s G31 work, always-on header-only graph attachment);
    G45's *target-kind* slice (a native `kind: header-only` in
    `project_targets.py`, distinct from `kind: library` requiring
    `binary_pattern`) is **not** landed — see §8.
  - **Recent contract-conflict reporting** — landed (`policy/contract_conflicts.py`,
    `workflows/contract_conflicts.py`, wired into `DiffResult.contract_conflicts`
    per `architecture/debt.yaml`'s own dated rationale entries).

## 2. Non-negotiable semantics — audit finding

No violation found in the code paths this pass actually touched
(`project_targets.py`, `run_plan.py`, `check-project.yml`,
`actions/check-target`). The one substantive finding is §3 below: a
declared `analysis.assurance` requirement was accepted and silently
unenforced, which is exactly the "an intentional break must not [silently]
become compatible" family of failure the vision's disposition rules exist to
prevent — here at the *assurance*, not the *finding*, layer: a project could
believe its CI gate required complete evidence when nothing checked that
claim at all.

## 3. First vertical slice: declared project checks executable and truthful

### What was found

At the review reference, `CheckSpec` (`abicheck/buildsource/project_targets.py`)
already accepts `id` and `analysis: {evidence, policy, assurance}` — this
task's premise is accurate. Tracing each field to its consumer:

- **`analysis.evidence`** — its own docstring already discloses the honest
  state: "Purely a distinguishing/reporting label at this phase; nothing
  downstream yet selects a different extraction pipeline based on this
  value." G39 (per-finding evidence-provider model, "Proposed; not
  started") is the real owner of ever making this selective. Not touched
  this pass — it does not claim an effect it doesn't have, so it is not the
  same class of defect as `assurance` below.
- **`analysis.policy`** — same shape: an identity slot for G42's future
  environment/provider-resolution work, not yet a second policy-selection
  mechanism. Not touched this pass for the same reason.
- **`analysis.assurance`** — **the real defect.** Its docstring claims
  "which assurance requirement this check's analysis must meet" — a much
  stronger claim than a reporting label. Verified by grep across the whole
  package: the value is parsed, structurally validated (non-empty,
  identifier charset), round-tripped through `RunPlanCheck.to_dict()`/
  `from_dict()`, emitted into the generated `run-plan.json`, and consumed
  **nowhere** — not by `check-project.yml`, not by `actions/check-target`,
  not by any report/gate code. A project author writing
  `analysis: {assurance: partial}` (or any other syntactically-valid
  identifier) got a config that validated cleanly and did nothing, forever.
  Only the literal string `"complete"` has ever mapped onto a real
  enforcement mechanism anywhere in this codebase — the pre-existing
  `compare`/`scan --against --require-complete-analysis` boolean gate
  (`abicheck/analysis_assurance.py`, `contract_coverage_exit.py`,
  wired all the way to the Action's own `require-complete-analysis` input
  and `action/run.sh`'s exit-code fold).

`derive_baseline_libraries()` and `check-project.yml`'s public-header
projection are real (`RunPlanCheck.header`, confirmed forwarded) but remain
one entry per target / a single workflow-global `header:` input as the task
states — that is G41 Phase 1/2's own open scope, not re-implemented here
(see §3's "Remaining backlog" below).

### What this pass implements (both halves)

1. **Truthfulness gate (reject before extraction).**
   `abicheck/buildsource/analysis_assurance_gate.py` (new leaf module,
   `SUPPORTED_ANALYSIS_ASSURANCE_VALUES = frozenset({"complete"})`,
   `analysis_assurance_issues()`) is called from `project_targets.py`'s
   `_check_issues` — the same deferred-validation point every other
   identifier-charset rule in that module uses. `project validate`/
   `project plan` now hard-reject (typed usage error, exit `64` for `plan`)
   any `analysis.assurance` value other than `"complete"`, before a run
   plan is even generated. Split into its own module purely to respect
   `project_targets.py`'s `architecture/debt.yaml` `no_growth` baseline
   (1812 lines) — see that module's own docstring.
2. **Real effect for the one supported value (plan → execution → gate).**
   Tracing `check-project.yml`'s matrix (`{'include': checks}`, i.e. the
   raw `run-plan.json` `checks[]` array) confirmed `matrix.analysis_assurance`
   was already available to every cell, but the "Run check-target" step
   never read it. **Both `check-project.yml` and `actions/check-target`
   live in this same repository** (a fact worth stating plainly since an
   earlier draft of this document's own G41 cross-reference wrongly assumed
   the consuming workflow lived in `abicheck/integration-lab` — corrected
   after reading `.github/workflows/check-project.yml` directly), so
   nothing blocked closing this within the session:
   - `check-project.yml`'s "Run check-target" step now passes
     `require-complete-analysis: ${{ matrix.analysis_assurance == 'complete' }}`.
   - `actions/check-target/action.yml` gained a matching
     `require-complete-analysis` input (default `'false'`), forwarded to
     the nested root-Action "Run analysis" step **gated on `kind != 'bundle'`**
     — a bundle check's operand is a directory (the resolved
     `binaries-dir`), and the root Action's own `require-complete-analysis`
     input already rejects a directory/package compare outright (no single
     `analysis_assurance` result to gate a release fan-out on); forwarding
     unconditionally would have turned a working bundle check into a hard
     operational error the moment any project declared `assurance: complete`
     anywhere in its `checks:`.
   - `docs/reference/check-target.md` gained a dedicated row (the input
     isn't a plain pass-through, so it doesn't belong in that page's
     generic mirrored-inputs row).

So the acceptance bar the task states — "Never report `analysis.assurance:
complete` as honored when no gate consumed it" — now holds two ways: an
*unsupported* value can no longer reach a run plan at all, and the one
*supported* value now genuinely reaches the pre-existing
`--require-complete-analysis` gate rather than being a label nothing reads.

### What this pass does **not** implement (explicit, not silently dropped)

- G41 Phase 1 (baseline consumer-context parity for `publish-baseline.yml`/
  `update-main-baseline.yml`) and Phase 2 (per-target header/compile-context
  projection replacing the single workflow-global `header:` input) — both
  large, separately-scoped phases with their own acceptance tests; not
  touched.
- The aggregate's compatibility/assurance/operational/missing-report
  failure-class distinction G41 Phase 3 also calls for — this pass makes
  the existing boolean floor *real*, it does not add the structured
  `assurance:` block or the aggregate's own failure-class reporting.
  `analysis.assurance` stays a scalar identifier, not the richer
  `{status, minimum_effective_depth, require_target_resolution,
  require_all_selected_translation_units}` shape G41 sketches as a later
  extension.
- `analysis.evidence`/`analysis.policy` remain unenforced labels — correctly
  so, per their own honest docstrings; G39/G42 are their real owners and
  neither is started.
- G41's Phase 4 (route real `dump` CLI execution through `DumpRequest`) is
  unrelated to this slice and untouched.

### Verification (this session, exact commands)

```bash
pip install -e ".[dev]"    # already present in this environment

# Targeted
pytest tests/test_project_targets_check_identity.py \
       tests/test_run_plan_check_identity.py \
       tests/test_project_targets.py tests/test_run_plan.py \
       tests/test_reusable_workflows.py \
       tests/test_reusable_workflows_require_complete_analysis.py \
       tests/test_reusable_workflows_project_evidence.py \
       tests/test_reusable_workflows_public_headers.py \
       tests/test_action_check_target.py \
       tests/test_action_check_target_explicit_id.py \
       tests/test_consumer_compile_full_chain_propagation.py -q
# -> 505+117 = passed (see individual runs below), 0 failed

# Full fast suite (parallel)
pytest tests/ -m "not integration and not libabigail and not abicc and not slow and not golden" \
       -q -n auto --dist worksteal
# -> 1 failed (tests/test_ai_readiness.py::test_main_returns_zero_on_clean_tree),
#    40598 passed, 39 skipped, 4 xfailed
# The one failure is a PRE-EXISTING, unrelated ADR-status-sync receipt-
# reachability issue (14 ADR '**Verified:**' commits not reachable from
# origin/main in this checkout) -- reproduced identically on unmodified
# `main` via `git stash` before re-running the same test, confirming it is
# not caused by this branch.

ruff check abicheck/ tests/            # All checks passed!
mypy abicheck/                         # Success: no issues found in 701 source files
python scripts/check_architecture.py   # Architecture: 0 error(s)
python scripts/check_ai_readiness.py   # 14 error(s) -- the same pre-existing
                                        # ADR-status-sync drift, verified
                                        # present on unmodified main; 143
                                        # warnings, all pre-existing
python scripts/check_docs_contract.py  # 0 error(s), 2 pre-existing warnings

ruff format --check abicheck/ tests/   # 599 files "would be reformatted" --
                                        # reproduced identically on
                                        # unmodified main (ruff 0.16.3, the
                                        # pinned version -- not a version
                                        # drift); every file this session
                                        # actually touched is already
                                        # correctly formatted
                                        # (ruff format <touched files> reports
                                        # them unchanged)
```

`scripts/verify.py --profile pr` was not run as one command in this
session (its `docs-build`/`distribution-build` steps need `mkdocs`/`build`/
`twine`, not installed here, and its `bugfix-test-contract` step needs a PR
body file) — the individual equivalent steps above (`lint`, `typecheck`,
`architecture`, `ai-readiness`, `docs-contract`, and the full unit suite)
were run directly instead, and are reported honestly rather than claimed as
a full `--profile pr` pass.

## 4. Converge analysis capabilities before retiring scan — ADR-068

**Re-verified, not restated.** ADR-068's own Status line ("Proposed — not
implemented") and `one-comparison-product.md`'s own Status line ("largely
unimplemented… Phase 1 item 2 (`FindingEvolution`) and Phase 1 item 3's
exit axes have since landed, as have Phase 2c, 2d and 2e") both match what
`main` actually contains at `f7b4fdcc7`: `scan` still exists as a full
second command family; `Change.evolution`/`DiffResult.resolved_findings`
exist (ADR-068 Phase 1 item 2's generic primitive); PR #1125 is actively
extending it (the same-comparison `evolve_check_findings()` matcher for
`private_header_leak`, D3's specific correctness risk). No contradiction
found; no gap not already recorded there. A one-line cross-reference was
added to `one-comparison-product.md`'s header pointing back to this
document, so a future audit doesn't have to re-derive that the two agree.

## 5. Scope and operand parity, not another bundle product

ADR-068 D2 ("One comparison product; baseline availability and cardinality
are scope, not command") is the owning decision;
[ADR-065](../adr/065-comparison-scope-selection-and-completeness.md) (S1-S4
implemented per its own Status line) is the scope-completeness primitive it
depends on. `scan --artifact-set` (ADR-056, **superseded** by ADR-068) still
exists in `main` today — ADR-068 D9 ("deletion follows callers, never
precedes them") explicitly permits this: the parity suite gating its
removal is `one-comparison-product.md`'s own Phase 3, not yet reached. No
new gap found beyond what that plan already tracks.

## 6. Finish scalable evidence collection and reuse

[G39](g39-per-finding-evidence-provider-model.md) ("Proposed; not started")
is the direct owner — it is the mechanism `analysis.evidence` (§3) would
eventually select between, once it exists. Separately, real caching
machinery already exists and is **not** a gap: `abicheck/buildsource/
build_cache.py` (`BuildEvidenceCache`, content-addressed L3 cache) and
`source_replay.py`'s `SourceAbiCache` (per-TU L4 cache, hit/miss
instrumented). What's missing, per G39's own problem statement, is a
*declared, per-finding* evidence-provider model spanning L0-L5 uniformly —
a design gap, not an unimplemented cache. No code changes; G39 remains the
correct, unstarted owner.

## 7. Governance, environments, and consumers

G42's "Named environments" section and
[G34](g34-producer-consumer-compiler-profile-separation.md) (producer/
consumer compiler profile separation, landed per G41's own confirmation
above) are the direct owners of the environment/governance half; the
consumer half is `vision-api-abi-evolution.md`'s workstream D
(consumer-specification), whose landed slice is visible directly in
`architecture/debt.yaml`'s own dated rationale for `appcompat.py`
("Workstream D-S1... `parse_app_requirements`/`scope_diff_to_app`/
`AppCompatResult` now accept a `model.consumer_spec.ConsumerSpec`"). G42's
own "Explicit check identifiers" half (§3's `id:`/`analysis:` fields) is
what this pass's vertical slice partially closes; its "Named environments"
half (`environment_id`, multi-environment fan-out) remains unimplemented,
confirmed by grep: no `environment_id` field exists on `CheckSpec`/
`RunPlanCheck` in `main` today. No code changes this pass; recorded as
still-open, correctly attributed to G42.

## 8. Header-only, Python, and reporting follow-ups

- **Header-only targets:** [G45](g45-header-only-targets-and-build-output-emit-helper.md)'s
  first gap — `kind: library` in `project_targets.py` still hard-requires
  `binary_pattern` (confirmed: `"target {target.id!r}: kind: library
  requires binary_pattern."` in `_check_issues`) — is unimplemented. No
  native `kind: header-only` target exists.
- **Header-only comparison itself** (not the *target-declaration* gap
  above) is largely landed: `header_graph.py`'s G31 work attaches a
  header-only `source_graph` on every header-parsing path unconditionally,
  confirmed present in `service.py`/`cli_dump_helpers.py`. Its own
  documented residual gap (`docs/contribute/plans/
  g31-header-graph-default-on-followup.md`, referenced from
  `header_graph.py`'s own module docstring) is `scan` and directory/package
  `compare` not yet building one — unchanged by this pass.
- **Python-API/reporting:** [ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md)
  is the typed-request/schema owner; its own Status line line was not
  independently re-derived beyond what §1 already confirmed (`CompareRequest`/
  `CompareResult` gaining fields tracked in `architecture/debt.yaml`'s own
  dated rationale, e.g. `changed_paths`/`abi3_floor` for ADR-068 Phase 2c/2d).
  No new gap found in the report/ package specifically for this task's
  §3 scope — `report/AGENTS.md`'s `compute_*`/`render_*` split was checked
  against and not touched, since this pass added no new report field.

## 9. Delivery discipline and cross-repository handoff

This is process, not a product surface — root `AGENTS.md`'s decision-making
principles (fix the cause not the instance; a bug-fix's regression test
targets the class) and `one-comparison-product.md` §6 (PR sequencing/
acceptance-test gating) are the owning conventions, followed directly in
this pass: the truthfulness gate targets the *class* of "accepted-but-
unhonored `CheckSpec.analysis_*` setting", stated as a reusable rule
(`analysis_assurance_gate.py`'s own docstring) rather than a one-off patch
for the `assurance` field alone, with `analysis.evidence`/`analysis.policy`
explicitly left alone because they don't share the defect (they disclose
their own non-effect). See the "Integration-lab handoff" section below for
the cross-repository half.

## Remaining backlog (not implemented this pass, by design)

In priority order for whoever picks this up next:

1. **G41 Phase 3, structured assurance + aggregate failure-class
   distinction.** The natural next slice on top of this pass: extend
   `analysis.assurance`'s supported-value set only once a second consumer
   motivates the richer `{status, minimum_effective_depth, …}` shape (G41's
   own "ship the minimal boolean slice first" guidance), and give the
   aggregate a real compatibility/assurance/operational/missing-report
   failure-class split.
2. **G41 Phase 1/2** — baseline consumer-context parity and per-target
   header/compile-context projection. Both are already fully scoped with
   acceptance tests in that plan; the largest remaining piece of §3's full
   acceptance scenario (two libraries with different installed/generated
   headers, a GCC-built artifact checked in both GCC-client and
   Clang-client contexts) depends on Phase 1/2 landing, not on anything
   this pass's slice touches.
3. **G39** — per-finding evidence-provider model, the real consumer
   `analysis.evidence` needs before it can mean anything beyond a label.
4. **G42 "Named environments"** — `environment_id`/multi-environment fan-out,
   unstarted.
5. **G45** — native `kind: header-only` project target.
6. **ADR-068/`one-comparison-product.md`** — continues independently via
   PR #1125 and the plan's own Phase 1-3 sequencing; no action needed from
   this audit beyond the cross-reference added.

## Integration-lab handoff

`abicheck/integration-lab` **was** reachable this session (a limitation the
task anticipated but did not occur): cloned read-only via the session's
`add_repo` tool at `b50d35773a6b7f020e5c8c57e2e567cccbb13d94`.

- Its `.github/workflows/project-shadow.yml` (and siblings) call
  `abicheck/abicheck/.github/workflows/check-project.yml@<pinned-sha>` —
  confirming `check-project.yml`/`actions/check-target` are genuinely this
  repository's own surface, not integration-lab's, which is what makes
  this pass's execution-side wiring (§3) possible without a cross-repo
  dependency.
- Its `.abicheck.yml` declares **no** `checks[].analysis:` block anywhere
  today — grepped directly, zero matches for `analysis:`/`assurance:`/
  `evidence:`/`policy:` inside a `checks:` context. So this pass's change
  is a no-op for integration-lab's *current* configuration: nothing there
  newly fails validation, and nothing there newly gates on
  `--require-complete-analysis`. This is a **newly supported scenario**,
  not a behavior change to an existing one.
- **Minimum upstream revision this work assumes:** none beyond
  `check-project.yml`'s existing pinned-SHA reference mechanism —
  integration-lab already pins the reusable workflow by commit SHA, so
  adopting this change is a normal SHA bump in whichever workflow file
  wants to opt a target into `analysis: {assurance: complete}`, with no
  other integration-lab-side change required.
- **Still blocked / out of this pass's scope for integration-lab
  specifically:** if integration-lab (or any consumer) later wants a
  *graduated* assurance level beyond the boolean `complete`/absent split,
  that needs G41 Phase 3's structured `assurance:` block (backlog item 1
  above) before this repository can honor it — declaring one today would
  correctly fail `project plan` under this pass's own truthfulness gate,
  which is the intended behavior, not a defect to route around.
