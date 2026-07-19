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

### P0.1 — Runtime warning when a mode-scoped input is set on an incompatible mode

**Problem:** `debug-info1/2`, `devel-pkg1/2`, `dso-only`,
`include-private-dso`, `keep-extracted`, `fail-on-removed-library`, `jobs`,
`abi-baseline`, `estimate`, and `audit` are all declared as
unconditional top-level inputs but each is only forwarded/consumed in a
subset of modes (`action/run.sh:387-407`'s `_is_release_style_operand()`
guard for the first seven; `run.sh:150-233` for `abi-baseline`;
`estimate`/`audit` are scan-mode-only). **Correction from an earlier draft
of this item:** `action.yml`'s `description:` text for all of these already
states the scope inline (e.g. `debug-info1`: "compare mode, directory/package
operands only"; `abi-baseline`: "for compare mode ... or scan mode";
`estimate`/`audit`: "scan mode only") — confirmed by re-reading `action.yml`
lines 49-76, 252-266, 284-289. So the documentation half of this item is
**already done**; the remaining gap is purely a *runtime* one: setting one of
these inputs on an incompatible `mode` produces no feedback at all today
(silent no-op), which a reader of the description text would only catch by
reading carefully, not by CI telling them.

**Change:** Add a `validate-inputs.sh` check that **warns** (job summary
annotation, not a hard failure — these are legal-but-inert combinations, not
errors) when a mode-scoped input is set on an incompatible `mode`.

**Files:** `action/validate-inputs.sh`, `tests/` — action shell-mapping
tests already exist per the audit; extend with cases asserting the new
warning fires/doesn't fire.

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

**Required caveat, flagged by review — do not skip:** the existing recipe
being promoted has every library in a multi-DSO release point at the *same*
shared `abicheck_inputs/` pack with no per-target projection check. ADR-045
§9 requires exactly that projection (`evidence.projection: "declared"` vs.
"inferred") before a per-target check may claim `effective_depth: source` —
but the validator that enforces it doesn't exist until P1.1
(`build-output.json` validator). If P0.4 lands (as a docs-only, no-code PR)
before P1.1 ships, promoting the recipe *as-is* to "the" canonical multi-DSO
page teaches exactly the anti-pattern §9 exists to prevent: claiming
source-depth evidence for every DSO from one unprojected, build-wide pack.
**This PR must add an explicit caveat to the promoted section** — e.g. "this
shared-pack recipe currently supports build-wide source audits and
per-target *header*-depth checks; claiming per-target *source*-depth
coverage from a shared pack requires the per-target projection validator
tracked in P1.1, not yet implemented" — rather than promoting the recipe
silently as if it already satisfies §9's safe model.

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
**Must include a shared-pack-across-two-targets case, corrected across two
review rounds:** a non-empty-only check would pass a `build-output.json`
whose two `targets[]` entries both point at the same `abicheck_inputs/`
pack marked `"declared"`. `abicheck.buildsource.inputs_validate._target_id_issues`
only compares TU `target_id`s against the pack's **own** `manifest.library`
field and explicitly does not flag untagged TUs — it has no parameter for
an externally-known expected target, so calling `validate_inputs_pack`
unmodified does **not** catch either (a) a legacy/untagged-TU pack shared
across targets, or (b) a pack whose `manifest.library` disagrees with which
`build-output.json` target actually references it. The validator needs a
real extension — either a new `expected_target_id` parameter on
`_target_id_issues`/`validate_inputs_pack`, or an equivalent comparison
performed in the new build-output validator using that function's existing
manifest/TU data — not a same-signature call to the function as it exists
today. Test cases: (1) two targets sharing one pack with untagged TUs must
fail, (2) a pack whose `manifest.library` doesn't match its referencing
`build-output.json` target must fail, both currently unenforced.

### P1.2 — `actions/resolve-baseline`

Implements ADR-045 §4/§6. New composite Action; consumes a baseline-set
archive/cache entry + `channel`/`target`/`profile` inputs; outputs a
resolved snapshot path or one of the five typed failure states.
**Bundle-scoped resolution requirement, flagged by review:** when the
resolved unit is a bundle (S14), the Action's output must be the staged
member **binaries** from the archive's `binaries/` directory (added to the
baseline archive per §6's S14 correction), not the `.abicheck.json`
snapshots — `abicheck/bundle.py:80-103`'s `build_bundle_snapshot()` reads
real ELF inputs and silently skips non-ELF (including JSON) ones, so
handing `check-target`'s bundle variant snapshot paths instead of binary
paths would make bundle analysis's old side silently empty, not error out.

**Files:** new `actions/resolve-baseline/action.yml`, `run.sh`. Reuses
`actions/baseline/build_manifest.py`'s manifest-reading logic — extract a
shared helper rather than duplicating the schema/digest-check code (avoid
recreating `IMPORT_CYCLE_ALLOWLIST`-style coupling; this is shell, not
Python import structure, but the same "don't duplicate the parsing logic"
principle applies — factor the manifest reader into
`abicheck/buildsource/`-adjacent Python invoked by both Actions' `run.sh` if
the logic is non-trivial).

**Tests:** shell-mapping tests for each of the five failure taxonomy rows
(ADR-045 §6 table); a bundle-scoped resolution fixture asserting binaries
(not snapshots) come back for a bundle target.

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

Implements ADR-045 §4/§7. Composes root `action.yml` + `collect-facts`
(**`phase: verify` for wrapper/clang-plugin evidence, `phase: auto` only
for `producer: replay`** — see ADR-045 §4's "collect-facts composition"
note, flagged by review: `check-target` runs after target
resolution/build-output exists, so it structurally cannot run
`collect-facts phase: prepare`, which must happen before the project's own
build. `check-single.yml`/`check-project.yml` document the caller's
required pre-build `collect-facts phase: prepare` step for S8/S9 as a
separate, earlier step — not something folded into `check-target`.) +
`resolve-baseline`; always emits the report envelope;
`gate-mode: local|deferred|advisory` input. **Identity requirement flagged
by review, corrected in a follow-up review pass:** `check-target` must write
the check's full `check_id` (`target@profile#baseline_channel`, §7) into the
report's own `target_id` field for **every** check, unconditionally — not
only when the run plan has more than one check for the same target. An
earlier version of this item scoped the rule to the multi-check case (S17
multi-profile, S21 multi-channel) "since it only matters once a target has
concurrent checks" — that reasoning was wrong: `aggregate.py`'s manifest
matching is an exact string comparison, so if the manifest projection (P1.4)
always emits `check_id`-shaped IDs but `target_id` is only sometimes set to
`check_id`, the *ordinary* single-check case (S1–S15's majority, including
PVXS) mismatches too (report says `target_id: "libpvxs"`, manifest expects
`"libpvxs@profile#channel"` — required target reported missing).
`abicheck/aggregate.py:642-729`'s `collect_reports` keys reports by
`target_id` (preferring the report's own field over the filename) and
hard-errors on a duplicate, so this identity must be exact and consistent
for every check, with no conditional branch.

**Second required sub-task, flagged by review:** `check-target`'s report
must populate `aggregate`'s *existing* verdict/gate fields, not only the new
ADR-045 §7 ones. `abicheck/aggregate.py`'s `parse_report_verdict` reads
top-level `verdict` (a `Verdict` enum string); its gate parsing
(`GateInfo.from_report_data`/`from_scan_report`) reads a `severity` block or
a scan report's own `exit_code`/`scan_schema_version` — none of these read
`compatibility_verdict` or `policy_gate_decision`, the new field names §7
introduces. Ship one of: (a) `check-target` dual-writes both the legacy
fields (`verdict`, `severity`/`exit_code`) *and* the new ones — the
lower-risk default, since it needs no `aggregate` code change — or (b) a
scoped `aggregate` parser update to also read the new field names. Either
way, this must land before P1.4, or `check-project.yml`'s `aggregate` step
will see every `check-target` report as verdictless/ungated.

**Files:** new `actions/check-target/action.yml`, `run.sh`.

**Dependencies:** P1.1, P1.2, P0.3.

**Tests:** end-to-end fixture workflow (`.github/workflows/test-action.yml`
already exercises the root action per AGENTS.md's tag-pinning note on that
file — extend it, don't create a parallel test harness); add a
multi-profile-same-target fixture case asserting `aggregate` does not
collide/error.

### P1.4 — `check-single.yml` / `check-project.yml` reusable workflows

Implements ADR-045 §4/§5 (`run-plan.json` generation + matrix + trailing
`aggregate` job for `check-project.yml`). **Includes a required sub-task
flagged by review**: `run-plan.json`'s `checks[]` schema is not
wire-compatible with `abicheck aggregate --manifest`'s existing
`{"targets": [{"id", "required"}]}` shape (`abicheck/aggregate.py:753-769`
hard-errors on anything else). The `check-project.yml` aggregate step must
project `run-plan.json` down to that shape before invoking `aggregate
--manifest` — using each check's `check_id` (not the bare target name) as
`targets[].id`, matching P1.3's report-identity requirement above, so S17/S21
don't collide in `aggregate`'s duplicate-target-id check. Implement as
either an inline `jq`/Python step in the workflow, or a small
`abicheck run-plan to-aggregate-manifest` CLI helper if the projection turns
out to need real validation logic beyond the `check_id` derivation. Decide
which during implementation; do not skip this and assume `run-plan.json` can
be passed straight through, and do not project down to bare target names.

**Second required sub-task, flagged by review:** in `gate-mode: deferred`
(ADR-045 §7), an individual matrix cell is *expected* to fail its own job on
an operational error — that visibility is the point. Plain GitHub Actions
`needs:` semantics skip a dependent job when any dependency fails, and a
skipped job reports `success` — so the trailing `aggregate` job in
`check-project.yml` **must** be defined with `if: always()` (or
`!cancelled()`), never a bare `needs:` with no `if:`. Without this, one
matrix cell's operational failure silently skips the aggregate job and the
branch-protection-required status goes green with a missing target —
exactly the failure mode ADR-045 is meant to close. Cover this with a
fixture-workflow test that deliberately fails one matrix cell and asserts
the aggregate job still runs and reports the failure.

**Files:** `.github/workflows/check-single.yml`, `.github/workflows/check-project.yml`,
possibly a new small CLI helper per the sub-task above.

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
ADR-045 §10). Both use `actions/baseline`'s existing publish contract
unchanged (it already documents itself as read-only/non-publishing —
`actions/baseline/action.yml:6-8` — so these workflows own the publish
step) **but `actions/baseline` itself is not unchanged — correction, flagged
by review:** today it only writes per-library `.abicheck.json` files plus
`manifest.json` (`actions/baseline/run.sh`, `actions/baseline/build_manifest.py`);
it has no code path that stages the member ELF binaries §6/§10's S14
correction requires for a bundle-scoped baseline archive's `binaries/`
directory. Without that change, P1.2's bundle-scoped `resolve-baseline` has
no producer for the binaries it must return — S14 bundle baselines fail at
resolution time (or worse, silently fall back to snapshots and lose old-side
bundle analysis, exactly the failure this correction exists to prevent).
**This item must therefore include a real `actions/baseline` code change**
(extend `run.sh`/`build_manifest.py` to also copy each bundle member's
source binary into `binaries/` and record its path/digest in
`baseline-set.json`) alongside the two new workflows — not treated as an
unrelated, already-solved dependency.

**Required cache-key detail, flagged by review:** GitHub Actions cache
entries are immutable once written (no overwrite-in-place); the workflow
must write a new key on every refresh — e.g.
`abicheck-baseline-main-<profile.id>-<head_sha>` — and `resolve-baseline`
must use `restore-keys: abicheck-baseline-main-<profile.id>-` to find the
latest match. A single stable key across refreshes silently stops updating
after the first write (the cache action treats it as a hit, not an error) —
this must be a tested behavior (a fixture asserting two consecutive
`update-main-baseline.yml` runs produce two distinct baselines resolvable
by `resolve-baseline`), not an assumption.

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

**Correction from an earlier draft, per review:** oneDAL PR #3693 is *not*
an unlocatable pilot — a repo-wide search for "Vandal" does return zero
matches (that part stands), but `docs/development/adr/044-reachability-aware-suppression.md`'s
Context section documents a real field review of oneDAL PR #3693 that found
a genuine tool-correctness defect and drove that ADR's entire redesign;
`docs/development/plans/g21-oneshot-deep-compare.md` and
`validation/REPORT.md` document the same evaluation's CLI-UX findings. That
review is real and valuable — but it is a **package/binary-level compare
evaluation** (conda-forge release artifacts, no source checkout, no build
reuse, no CI workflow), not a **GitHub-Actions CI-integration pilot** in
PVXS's sense (ADR-045 §"What the audit found," finding 5). **The remaining
backlog item is narrower than "find a second pilot from scratch":**

- Identify and get access to a second real C/C++ project — possibly oneDAL
  itself, revisited with a CI-integration lens this time, or a different
  project — with: a vendor compiler/toolchain (icpx/SYCL or MSVC), multiple
  DSOs with distinct public surfaces, an existing expensive build worth
  reusing, and (ideally) an existing libabigail or ABICC gate to migrate
  alongside.
- Produce a validation report in the same format as
  `validation/pvxs-abi-validation-2026-07.md` — defects found/fixed,
  documented-not-fixed issues, a recommended workflow — before claiming any
  S9/S15/S17/S21/S26 acceptance criteria are met for a vendor-toolchain
  project. Until that report exists, treat those scenario rows in ADR-045
  §8 as **design-validated against PVXS's simpler case only**, not proven
  for the vendor-toolchain/multi-baseline-channel class — oneDAL's existing
  field review does not substitute for it, however useful its own findings
  were.

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
