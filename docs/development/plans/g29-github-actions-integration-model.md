# G29 — GitHub Actions Integration Model: Project Lifecycle Backlog

**ADR:** [ADR-045](../adr/045-github-actions-integration-model.md)
**Type:** Initiative plan (multi-phase); no `usecase-registry.yaml` entries yet
— GH-Actions integration is a cross-cutting CI/UX surface, not a detector
capability, so it is tracked here rather than in the registry (consistent
with how G19/G24 track their own initiative work).
**Effort:** XL (phased P0/P1/P2) · **Risk:** medium — new schemas and two
new composite Actions are additive, not breaking, but the documentation
reorganization touches most of `docs/user-guide/`.

## Problem

ADR-045 records the target domain model and component surface: a project
integration lifecycle (config → build → evidence → target/baseline
resolution → check → report → optional fan-in → baseline publish) that
demotes `abicheck aggregate` from an implicit architectural center to one
scenario (S28) among 28. This plan is the sequenced backlog that gets there
without one large rewrite PR, plus the pilot-validation plan the ADR's
decision log (D9) flags as an open gap.

## Sequencing principle

Each phase below should land as **multiple small, independently reviewable
PRs**, not one PR per phase. The suggested PR boundaries are listed under
each item. No PR should combine a schema addition with a documentation
reorganization — those are reviewed differently and by different failure
modes (schema PRs need `tests/test_verify_profiles.py`-style contract tests;
doc PRs need `mkdocs build --strict` + `check_ai_readiness.py`).

---

## P0 — Onboarding blockers (no architecture change required)

These fix real defects the audit (ADR-045 §"What the audit found")
identified in the *existing* surface. None require the new primitives.

### P0.1 — Input-scoping documentation and validation for `action.yml`

**Problem:** `debug-info1/2`, `devel-pkg1/2`, `dso-only`,
`include-private-dso`, `keep-extracted`, `fail-on-removed-library`, `jobs`
are declared unconditionally but only forwarded by `run.sh` inside
`_is_release_style_operand()` (`action/run.sh:387-407`); `abi-baseline` is
resolved but silently inert on `mode: dump`/`deps-tree`/`deps-compare`
(`run.sh:150-233`); `estimate`/`audit` are undeclared as scan-only.

**Change:** Add a `validate-inputs.sh` check that **warns** (job summary
annotation, not a hard failure — these are legal-but-inert combinations, not
errors) when a mode-scoped input is set on an incompatible `mode`. Update
`action.yml` input `description:` text for each of the 9 flagged inputs to
state its actual scope inline (today the description text doesn't say
"release/package mode only" or "compare/scan mode only").

**Files:** `action/validate-inputs.sh`, `action.yml` (description text
only), `tests/` — action shell-mapping tests already exist per the audit;
extend with cases asserting the new warning fires/doesn't fire.

**Tests:** New `test_action_input_scope_warnings` case(s) in the existing
Action shell-mapping test suite (mirroring however `debug-info1` forwarding
is already tested).

**Docs:** `docs/user-guide/github-action.md`'s input table gains a "Scope"
column.

**PR boundary:** one PR — shell + input descriptions + tests together (small
enough not to split further).

### P0.2 — `collect-facts` `phase: auto` fail-loud for wrapper/plugin

**Problem:** `phase: auto` silently only runs `prepare` for
`producer: wrapper`/`clang-plugin` (`actions/collect-facts/run.sh:714-716`)
— a caller who doesn't realize this ends up with an unverified pack and no
error.

**Change:** When `phase: auto` resolves to `producer: wrapper` or
`clang-plugin`, emit an explicit job-summary notice *and* set a
`collect-facts` output (`auto-completed: false`) a caller can branch on,
instead of a print-only notice. Document the two-step choreography
explicitly in `docs/user-guide/producing-source-facts.md` rather than
implying `auto` is always one step.

**Files:** `actions/collect-facts/run.sh`, `actions/collect-facts/action.yml`
(new output), `docs/user-guide/producing-source-facts.md`.

**Tests:** Extend `actions/collect-facts`'s existing shell tests to assert
the new output value per producer.

**PR boundary:** one PR.

### P0.3 — Report identity envelope (subset of ADR-045 §7)

**Problem:** JSON reports don't carry `check_id`/`profile_id`/
`requested_depth`/`effective_depth`/`baseline_channel` today — a P1
prerequisite, but valuable standalone since it's what makes `aggregate`'s
existing coverage/gate logic auditable.

**Change:** Add the identity fields from ADR-045 §7 to the existing
`compare`/`scan` JSON report schema as **additive, optional** fields (schema
version bump, backward compatible — old consumers ignore unknown fields).
Do *not* yet build `resolve-baseline`/`check-target` (P1) — this item only
makes the fields available so P1's primitives have something to populate.

**Files:** `abicheck/reporter.py`, `abicheck/checker_types.py` (or wherever
`DiffResult`/report serialization lives), schema files under wherever report
JSON schemas are versioned, `abicheck/serialization.py`.

**Tests:** Schema round-trip tests; `tests/test_verify_profiles.py`-style
schema-contract test if one doesn't already assert report schema stability.

**Migration:** additive only — `changelog.d/` fragment required (touches
`abicheck/**/*.py`) per AGENTS.md.

**PR boundary:** one PR for the schema/model change, a separate PR to wire
`requested_depth`/`effective_depth` population through the CLI (depends on
PR #601's `DumpDepthNotSatisfiedError` work landing first per ADR-045 §11.2
and the repo's existing Known Gaps entry — do not duplicate that
enforcement, extend it).

### P0.4 — Canonical single-library and multi-DSO doc pages

**Problem:** multi-DSO guidance is split three ways with no single canonical
page (ADR-045 finding 4).

**Change:** Promote `docs/user-guide/github-action-source-scans.md`'s
"Recommended flow: multi-library release with one shared facts pack"
section to the canonical multi-DSO recipe; the other two pages
(`github-action.md`, `github-action-recipes.md`) link to it instead of
restating it. No new scenario content yet — this is de-duplication, not the
full scenario-first IA (that's P1's `docs/integration/` tree).

**Files:** the three docs pages listed; `mkdocs.yml` nav unaffected (no new
pages yet).

**Tests:** `mkdocs build --strict`; `check_ai_readiness.py`'s
`mkdocs-nav-coverage` and `doc-count-sync` checks.

**PR boundary:** one PR, docs-only.

---

## P1 — Integration model (ADR-045's new primitives)

### P1.1 — `build-output.json` schema + validator

Implements ADR-045 §2/§11.1. New schema module (e.g.
`abicheck/buildsource/build_output.py` or a sibling of `inputs_pack.py`,
following the existing `abicheck/buildsource/CLAUDE.md` module-table
convention), plus `python -m abicheck build-output validate <dir>` CLI
subcommand (or `abicheck buildsource validate-output`, matching existing
`cli_buildsource.py` command-family naming). No producer tooling yet (that's
P1.2) — this PR defines the contract and validates a hand-authored example.

**Files:** new `abicheck/buildsource/build_output.py` (or similarly named
per existing conventions), new `abicheck/cli_buildsource.py` subcommand,
`docs/reference/build-output-schema.md` (new).

**Tests:** unit tests for the validator's failure taxonomy (empty declared
root, digest mismatch, `projection` inconsistency — ADR-045 §11.1).

### P1.2 — `actions/resolve-baseline`

Implements ADR-045 §4/§6. New composite Action; consumes a baseline-set
archive/cache entry + `channel`/`target`/`profile` inputs; outputs a
resolved snapshot path or one of the five typed failure states.

**Files:** new `actions/resolve-baseline/action.yml`, `run.sh`. Reuses
`actions/baseline/build_manifest.py`'s manifest-reading logic — extract a
shared helper rather than duplicating the schema/digest-check code (avoid
recreating `IMPORT_CYCLE_ALLOWLIST`-style coupling; this is shell, not
Python import structure, but the same "don't duplicate the parsing logic"
principle applies — factor the manifest reader into
`abicheck/buildsource/`-adjacent Python invoked by both Actions' `run.sh` if
the logic is non-trivial).

**Tests:** shell-mapping tests for each of the five failure taxonomy rows
(ADR-045 §6 table).

### P1.3 — `actions/check-target`

Implements ADR-045 §4/§7. Composes root `action.yml` + `collect-facts` (if
evidence required) + `resolve-baseline`; always emits the report envelope;
`gate-mode: local|deferred|advisory` input.

**Files:** new `actions/check-target/action.yml`, `run.sh`.

**Dependencies:** P1.1, P1.2, P0.3.

**Tests:** end-to-end fixture workflow (`.github/workflows/test-action.yml`
already exercises the root action per AGENTS.md's tag-pinning note on that
file — extend it, don't create a parallel test harness).

### P1.4 — `check-single.yml` / `check-project.yml` reusable workflows

Implements ADR-045 §4/§5 (`run-plan.json` generation + matrix + trailing
`aggregate` job for `check-project.yml`).

**Files:** `.github/workflows/check-single.yml`, `.github/workflows/check-project.yml`.

**Dependencies:** P1.3.

### P1.5 — `.abicheck.yml` `targets:`/`profiles:`/`baseline:` block

Implements ADR-045 §3. Config schema extension + `abicheck/policy_file.py`
(or wherever `.abicheck.yml` is parsed) support; `docs/reference/config-file.md`
update.

**Dependencies:** none of the above strictly, but should land before P1.4 so
`check-project.yml`'s matrix generation has a real config source to read.

### P1.6 — `publish-baseline.yml` / `update-main-baseline.yml`

Implements ADR-045 §6/§10. `publish-baseline.yml`: release-triggered,
`actions/baseline` → atomic archive → release-asset upload.
`update-main-baseline.yml`: default-branch-push-triggered, targets the
`accepted-main` channel's storage backend (Actions cache by default per
ADR-045 §10). Both use `actions/baseline` unchanged (it already documents
itself as read-only/non-publishing — `actions/baseline/action.yml:6-8` — so
these workflows own the publish step, matching that existing contract).

**Dependencies:** P1.1, P1.5.

### P1.7 — Scenario-first documentation IA

Implements ADR-045 §8's scenario catalog and the task's requested
`docs/integration/` tree. **File tree and migration map:**

```
docs/integration/
  index.md                                  # NEW — the "answer these questions" landing page
  concepts.md                               # NEW — glossary (ADR-045 §1's table, prose form)
  scenarios/
    single-library.md                       # NEW — absorbs github-action.md quick-start (S1)
    existing-build-artifact.md              # NEW — S3, the preferred large-repo flow
    header-aware-check.md                   # NEW — absorbs relevant scan-levels.md section (S6)
    source-replay.md                        # NEW — absorbs github-action-source-scans.md (S7)
    build-integrated-facts.md               # NEW — absorbs producing-source-facts.md (S8, S9)
    single-build-audit.md                   # NEW — absorbs choose-your-workflow.md's audit path (S5)
    multi-dso-project.md                    # NEW — the P0.4-promoted canonical page (S15)
    release-bundle.md                       # NEW — absorbs multi-binary.md's bundle framing (S14)
    packages-and-sdks.md                    # NEW — absorbs github-action-recipes.md's package section (S13)
    multi-platform.md                       # NEW — absorbs recipes.md's matrix section (S17)
    cross-compilation.md                    # NEW — absorbs recipes.md's cross-compile section (S18)
    application-and-plugin-contracts.md     # NEW — S22, S23
    dependency-and-container-checks.md      # NEW — absorbs deps-tree/deps-compare docs (S24)
    monorepo.md                             # NEW (S25)
    migration-and-rollout.md                # NEW — absorbs ci-gating.md's rollout guidance (S26, S27)
  baselines/
    lifecycle.md                            # NEW — ADR-045 §6, prose form
    release-contract.md                     # NEW (S19)
    accepted-main.md                        # NEW (S20)
    baseline-sets.md                        # NEW — schema reference
    storage.md                              # NEW — ADR-045 §10 table, prose form
  reference/
    actions.md                              # NEW — replaces scattered per-Action doc sections
    reusable-workflows.md                   # NEW
    project-config.md                       # supersedes reference/config-file.md's GH-specific parts
    build-output-schema.md                  # from P1.1
    report-schema.md                        # from P0.3
    failure-semantics.md                    # NEW — the resolve-baseline taxonomy + report envelope axes
```

**Migration map for existing pages:** `choose-your-workflow.md` stays as the
CLI-command-level decision tool (it already serves that job well per the
audit) and gains a link to `docs/integration/index.md` as the
GH-Actions-specific front door; `github-action.md` becomes the input/output
*reference* only (content moves to `reference/actions.md` +
`scenarios/single-library.md`); `github-action-recipes.md` is retired, its
content distributed into the relevant `scenarios/*.md` pages per the mapping
above (`tests/` or a redirect-check script should assert no orphaned
inbound links remain — reuse `check_ai_readiness.py`'s `mkdocs-nav-coverage`
check, which already flags unlinked pages); `github-action-source-scans.md`,
`baseline-management.md`, `producing-source-facts.md`,
`build-evidence-setup.md` are retired with content distributed similarly;
`scan-levels.md`, `multi-binary.md`, `ci-gating.md`, `real-world-example.md`,
`concepts/build-source-data.md`, `concepts/evidence-and-detectability.md`
are **kept as-is** (per `docs/CLAUDE.md`'s explicit note that the L0-L5
evidence trio and exit-code reference are deliberately single-sourced
elsewhere) — `docs/integration/` pages link to them rather than duplicating.

**This is the single largest item in the backlog** and should itself be
split into ~4-5 PRs (index+concepts; scenarios/ batch 1 — S1/S3/S6/S7;
scenarios/ batch 2 — S8/S9/S13/S14/S15; scenarios/ batch 3 — remainder;
baselines/ + reference/), each verified independently against
`mkdocs build --strict` and the AI-readiness `mkdocs-nav-coverage` /
`adr-index-nav-sync` (n/a here, doc-count-sync applies) checks.

---

## P2 — Deeper architecture (not started here)

- **Full TU→link-unit→DSO source-evidence attribution** (ADR-045 §9/D8) —
  needs linker-invocation capture, extending
  `abicheck/buildsource/build_query.py`'s existing partial zero-config
  compile-DB inference. Its own follow-up ADR when undertaken.
- **Monorepo changed-component planning** at scale (S25's `run-plan.json`
  filtering beyond a simple path-prefix diff).
- **Richer cross-platform baseline storage** (external object store backend,
  ADR-045 §10's fourth row) — no P0/P1 user story currently justifies it.
- **Provider plugins for build systems** beyond the CMake/Bazel/Make
  adapters `abicheck/buildsource/adapters/` already has.
- **Generalized external artifact stores** for baseline sets beyond GitHub
  Release/Actions cache/git.

---

## Pilot validation plan

### PVXS (confirmed pilot — extend, don't re-validate from scratch)

`validation/pvxs-abi-validation-2026-07.md` already validates the core
scanning correctness (3 real defects found and fixed) and proposes a
two-library `compare` workflow. **New validation needed once P1 lands:**
re-run the pilot using `check-project.yml` + `.abicheck.yml`'s `targets:`
block instead of the hand-written directory-fan-out `compare` workflow the
existing report recommends, and confirm:

- The existing Make-based build is reused unmodified (S3/S11 acceptance).
- `libpvxs`/`libpvxsIoc` are correctly modeled as two `targets:` under one
  `bundles:` entry, each keeping its own `public_headers:` scope
  (`--scope-public-headers` — finding F3 in the existing report).
- `resolve-baseline` produces per-target reports distinguishable in the PR
  UI (two `check_id`s, ADR-045 §8 S21 row).
- Fast-PR default does not force full source-depth scan (F1's O(N²)
  perf-bug fix should keep this affordable, but the *policy default*
  — changed-scope, not full-unseeded — is a separate acceptance check).
- The existing `abi-dumper`/ACC flow (already running per the pilot's own
  recommendation) can run in parallel as a `gate-mode: advisory` burn-in
  lane without modification.

### Second complex pilot — open gap (ADR-045 D9)

No second pilot with a locatable validation record exists in this
repository today (confirmed: zero matches for "Vandal"; oneDAL appears only
as a scan-timing data point built from conda-forge release binaries, not a
submitted integration PR with an acceptance report). **This is a real
backlog item, not a documentation gap to paper over:**

- Identify and get access to a second real C/C++ project with: a vendor
  compiler/toolchain (icpx/SYCL or MSVC), multiple DSOs with distinct public
  surfaces, an existing expensive build worth reusing, and (ideally) an
  existing libabigail or ABICC gate to migrate alongside.
- Produce a validation report in the same format as
  `validation/pvxs-abi-validation-2026-07.md` — defects found/fixed,
  documented-not-fixed issues, a recommended workflow — before claiming any
  S9/S15/S17/S21/S26 acceptance criteria are met for a vendor-toolchain
  project. Until that report exists, treat those scenario rows in ADR-045
  §8 as **design-validated against PVXS's simpler case only**, not proven
  for the vendor-toolchain class.

### Minimal generic pilots (P1 exit criteria)

Each should record: initial integration LOC/YAML complexity, custom shell
line count, build duplication (did abicheck rebuild anything the project's
CI already builds), wall time, evidence depth achieved, report quality,
failure behavior on a deliberately broken case, and remaining manual steps
— the same "ease of enablement" measurements ADR-045/the task both call for,
not just correctness:

- Simple CMake single-library repository (S1/S6 acceptance).
- Make/custom-build repository — can reuse PVXS's own build if a second,
  simpler EPICS module or a synthetic Make fixture is used instead
  (S11 acceptance, distinct from the full PVXS pilot above).
- Bazel repository (S12 acceptance) — no existing pilot found for this;
  needs a fixture or a real small Bazel C++ project.
- Package-only RPM/Deb/tar comparison (S13 acceptance).
- Linux/macOS/Windows matrix (S17 acceptance) — the existing CI matrix
  (ADR-045-unrelated, `.github/workflows/ci.yml`) already exercises
  cross-platform *parsing*; this pilot is specifically about the
  *integration workflow* (`check-project.yml` multi-profile matrix), a
  distinct claim.
- Cross-compiled target (S18 acceptance).

---

## Out of scope for this plan

- Any change to detector logic, `ChangeKind` taxonomy, or snapshot schemas —
  this plan is integration-surface only.
- The P2 items listed above — recorded for visibility, not scheduled.
- Retrofitting the full source-evidence attribution model (D8) into P1's
  `build-output.json` — P1 ships the safe/declared-or-build-wide model only.
