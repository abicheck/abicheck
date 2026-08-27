# AGENTS.md — `.github/`

CI/CD workflows, the composite Action's manifest, issue/PR templates, and
review ownership. See the repository root `/AGENTS.md` for the canonical
project-wide contract — this file only covers what's specific to this tree.

## Required vs. informational workflows

Not every workflow here blocks a merge. Before assuming a red check means
"fix this before merging," check which bucket it's in:

| Workflow | Required on every PR? | Notes |
|----------|------------------------|-------|
| `ci.yml` | **Yes** — `ai-readiness`, `fair-metadata`, `lint-and-types`, `unit-tests` (canonical Linux/3.13 lane), `packaging` jobs | The core gate. `unit-tests`' `integration-tests`/`windows-msvc` sibling jobs in the same workflow have their own rules below. |
| `changelog-check.yml` | Yes, only when the diff touches `abicheck/**/*.py` | Bypass with the `skip-changelog` label |
| `cli-interface-check.yml` | Yes, when the CLI surface changes | Diffs `dump_cli_surface.py` output old vs. new |
| `dependency-review.yml` | Yes | GitHub's built-in dependency-review action |
| `docs-pr.yml` | Yes, when `docs/**`/`mkdocs.yml` changes | |
| `docs-review-triggers.yml` | No (informational) | Diffs the PR's changed files against every docs page's front-matter `depends_on` list (`scripts/check_docs_review_triggers.py`) and posts an `::notice::`/step-summary when they overlap — a nudge to re-check that page, never a merge blocker. |
| `security.yml` | Yes | CodeQL + related static checks |
| `ci.yml`'s `windows-msvc` job | No — `continue-on-error: true` | MSVC+PDB lane is still maturing; informational only |
| `ci.yml`'s `heavy-parity-gate` → `libabigail-parity`/`abicc-parity`/`integration-tests` | Conditional | Only runs when one of `abicheck/**`, `tests/**`, `examples/**`, `.github/workflows/**`, `pyproject.toml`, `action/**`, `.github/actions/**`, or `scripts/verify.py` changed (path-filtered via `dorny/paths-filter`). `integration-tests` joined this gate rather than staying always-on because it is the most expensive job in the repo (three OS legs, macOS billed 10x) and was running in full on docs-only PRs. The last four paths are the gated jobs' own *infrastructure* — they all `pip install -e ".[dev]"` and set CastXML up via the composite action, and the parity lanes are driven by `scripts/verify.py`'s step catalog — so omitting them let a re-pin of CastXML merge without a single CastXML-using job running. |
| `clang-plugin.yml` | **No** | Standalone, path-filtered to `contrib/abicheck-clang-plugin/**`; never a required abicheck-CI gate (see `contrib/abicheck-clang-plugin/AGENTS.md`) |
| `mutation.yml` | No | **Auto-runs on any PR touching a mutated module** (path-filtered to `[tool.mutmut].only_mutate` **and each module's own `tests/test_<stem>*.py`** — a naming-convention match, not a complete one: 257 of ~450 test files import a mutated module directly, so a complete trigger is effectively `tests/**`, i.e. a two-hour job on most PRs; the weekly run and the `mutation` label stay the complete checks — a PR that only weakens a detector test changes no production file, so the source-path entries alone never started the lane; both halves kept in sync by `tests/test_mutation_workflow_contract.py`), gating `--diff-scoped`: a surviving mutant in a function the branch changed fails, with no baseline needed. A test-only diff has no changed function to scope to, so such a run says it gated nothing rather than printing OK — that case is only checkable as drift, and when the trigger was a *detector test* — or the `mutation` label, which is documented as the complete check — the PR lane passes `--require-baseline` so it fails closed instead of going green on a run that checked nothing. Recording `mutation-baseline.json` once (dispatch, `write_baseline: true`) is what turns that failure into a real gate. Also weekly (per-module baseline drift, `--require-baseline`) and on dispatch (record the baseline). The `mutation` label still forces a run on a PR the path filter misses. |
| `performance.yml` | Partially | Runs on PRs touching detector-core files; see `docs/contribute/performance.md` |
| `examples-validation.yml` / `-nightly.yml` | No | Scheduled/manual regression sweeps over the example catalog |
| `eval-suite.yml` | No | Real-world regression guard, scheduled |
| `realworld-validation.yml` | No | conda-forge package validation harness |
| `agentready.yml` | No (informational) | Runs the external AgentReady structural scanner; posts SARIF/step-summary. Distinct from — and does not replace — `scripts/check_ai_readiness.py`, which enforces abicheck-specific invariants (ChangeKind partition, doc-count sync, import cycles, ...). See root `AGENTS.md`'s "AI-readiness gate" section. |
| `test-action.yml` | Yes, when `action/**`/`action.yml` changes | See `action/AGENTS.md` |
| `bugfix-test-contract.yml` | Yes, on `fix:`/`perf:`/`security:` PRs | Structural half: a fix changing shipped code must change a test. Declared half: the PR body must answer the bug-fix test contract, plus any conditional the diff triggers. Bypass with the `skip-test-contract` label. See `scripts/check_bugfix_test_contract.py` for what each answer is for. |
| `publish.yml` / `pages.yml` | N/A (release/deploy only) | Not PR gates |
| `verify-merge-checks.yml` | N/A (post-merge only, `push: main`) | Not a PR gate — see "Required-status-check configuration" below. |

## Required-status-check configuration (CLI cleanup phase two, PR A / PR 0B)

The table above states which workflows are *supposed to* gate a merge; this
section is the mechanical rule for turning that into a real GitHub
required-status-checks list (Ruleset or classic branch protection), and the
two things this repo added so a path-filtered "Yes, when X changes" row can
actually be required without stranding every PR that doesn't touch X.

**The rule**, applied fresh against the table above at configuration time —
not a hand-copied snapshot, which drifted wrong three times in a row before
this was written down as a rule instead of a list (see the CLI cleanup
phase-two plan's own PR 0B section for that history):

1. For every workflow the table marks required (unconditionally or "when X
   changes"), read its own `on: pull_request:` block.
2. **No `paths:` filter** (the workflow always runs on a PR against `main`;
   an internal diff check or label decides applicability instead) → require
   its own check name directly. `changelog-check.yml`/`cli-interface-check.yml`/
   `bugfix-test-contract.yml`/`dependency-review.yml`/`security.yml`/`ci.yml`
   are all in this bucket.
3. **Has a `paths:` filter** (the workflow may not run at all on an
   unrelated PR) → never require its own check name directly — no native
   GitHub mechanism (classic branch protection or a Ruleset's required-
   status-checks rule) conditions "required" on which paths a given PR
   touched, so doing so strands every PR that doesn't touch that path.
   `docs-pr.yml` and `test-action.yml` are in this bucket, and each has a
   **neutral-aggregate gate job living in `ci.yml`** instead
   (`docs-pr-required`/`test-action-required`, both unconditioned like every
   other `ci.yml` job): each re-evaluates the exact same `paths:` filter its
   target workflow's own trigger uses; when the paths don't match, there is
   nothing to gate and the job succeeds immediately; when they do match, the
   target workflow is guaranteed to have been triggered by the same
   `pull_request` event, so the gate job polls that workflow's own aggregate
   check (`build-docs` for `docs-pr.yml`; the added `test-action-summary`
   job's `test-action summary` check for `test-action.yml`, since that
   workflow fans out to 17+ independent jobs with no single existing
   pass/fail check to point at) for the same head SHA and mirrors its
   conclusion. **Require the gate jobs' own emitted check names in the
   Ruleset — `docs-pr (required)`/`test-action (required)` (each job's own
   `name:` override, not its job id `docs-pr-required`/`test-action-required`;
   GitHub Rulesets match a required status check by its reported check-run
   name, not by workflow job id) — never `build-docs`/`test-action summary`
   directly.** The whole point is that the wrapper is unconditionally present
   while the wrapped check may not be.
4. Anything the table marks **not required** stays out of the required set
   entirely — this rule does not change that classification.

**The required-check list, applying this rule to the table above (2026-08,
`main` at the CLI cleanup phase-two governance PR):** `ai-readiness`,
`fair-metadata` (check name `FAIR metadata and packaging`), `lint-and-types`,
`unit-tests (ubuntu-latest, 3.13, false)` (the canonical Linux/3.13 lane —
not every matrix leg, see "one stable aggregate check" below),
`packaging (ubuntu-latest)`, `packaging (windows-latest)`,
`changelog-fragment`, `cli-interface-diff`, `test-contract`,
`Dependency Review`, `Security Scan`, `CodeQL Analysis (python)`,
`docs-pr (required)`, `test-action (required)` (the `name:` values of
`ci.yml`'s `docs-pr-required`/`test-action-required` jobs — see the note in
rule step 3 above on why the check name, not the job id, is what a Ruleset
actually requires).

**This document states the rule and the resulting list; it does not itself
turn the list into an enforced Ruleset.** That configuration step is a
repository-admin action (GitHub Settings → Rules, or the REST/GraphQL
Rulesets API with an admin-scoped token) outside what an automated PR can
carry out — apply the list above there by hand, or with `gh api` /
equivalent tooling that holds that access, and re-derive it from this
section's rule (not by re-copying this list verbatim) if the table above has
since changed.

**`branch-protection-ruleset.json`/`branch-protection-ruleset.md`** in this
same directory are the ready-to-apply artifact for that step: an exact
Rulesets API payload for the 14-name list above, one `gh api` command to
apply it, and a negative-test procedure to confirm enforcement is real (not
just configured). `tests/test_required_checks_governance.py`'s
`TestBranchRulesetArtifact` keeps the JSON's context list in lockstep with
this section's prose and `verify-merge-checks.yml`'s own `REQUIRED_CHECKS`
array, so this is a third mechanically-checked copy of the list, not a
fourth hand-copied one. The admin action itself is still outstanding — see
`docs/contribute/plans/cli-cleanup-phase-two.md`'s PR 0B status note.

**Prefer one stable aggregate required check per always-required workflow**
over requiring every matrix leg individually — a matrix-leg-level required
list goes stale on every matrix edit and is the usual reason required checks
get turned back off. `test-action.yml`'s own `test-action-summary` job
(`needs:` every job in that workflow, `if: always()`) is exactly this for a
fan-out workflow with no single existing pass/fail check; `docs-pr.yml`
already has only one job (`build-docs`) and needed no separate aggregate.

**Exact-merge-SHA verification (item 4).** `verify-merge-checks.yml` runs on
every push to `main` and looks up the PR GitHub associates with that commit
(covers squash/merge/rebase merges alike), then re-checks that PR's own
already-recorded *tested head SHA* — not the new merge commit, which most of
the required workflows above never re-run against (`pull_request`-only
triggers) and which wouldn't prove anything about what was reviewed even if
they did — against the required-check list above, including the two
neutral-aggregate gate checks themselves (`docs-pr (required)`/
`test-action (required)`, which are unconditioned and so exist on every PR
head SHA); only the *path-filtered* checks those gates wrap (`build-docs`,
`test-action summary`) are deliberately excluded from this second pass, since
whether those exist at all depends on the same path filter the gate job
already re-evaluated as a required check on the PR itself — see the
workflow's own header comment for the full reasoning. It cannot block an
already-completed merge; it fails loudly on `main`'s own Actions tab instead,
which is what makes a merge that slipped through a misconfigured or
momentarily-disabled Ruleset *detectable* rather than invisible — the exact
gap that let the PR #782 merge SHA go out with no full `ci.yml` sweep having
run against it.

## Local equivalence (CLAUDE.md "M0-3")

`ci.yml`'s always-required jobs (`ai-readiness`, `fair-metadata`,
`lint-and-types`, and the canonical `unit-tests` Linux/3.13 lane) are exactly
what `python scripts/verify.py --profile pr` runs locally —
`tests/test_verify_profiles.py` asserts the two stay in sync. **Don't add a
new required check to `ci.yml` without adding the matching `Step` to
`scripts/verify.py`'s catalog** — an agent that only runs the local `pr`
profile and gets a clean result should never be surprised by a required CI
job it had no way to reproduce.

## Editing a workflow

- Prefer routing a new pass/fail gate through `scripts/verify.py` (add a
  `Step`, then call `python scripts/verify.py --profile <profile> --only
  <name>` from the job) over inlining a fresh raw command — see
  `scripts/CLAUDE.md`'s "Adding a new script" section.
- Action pins are inconsistent across workflows: some steps pin by commit SHA
  (e.g. `actions/upload-artifact@ea165f8d...`), most use a floating major tag
  (`actions/checkout@v6`). If you're touching a workflow that executes
  untrusted input or has write permissions, prefer pinning by commit SHA
  rather than copying the floating-tag style from nearby steps.
- `CODEOWNERS` currently routes every path to one owner — it exists for
  auto-assignment, not differentiated review policy. Don't assume a
  `.github/`, release, or security-relevant change gets extra scrutiny by
  default; call it out explicitly in the PR description if it needs it.

## Issue/PR templates

`PULL_REQUEST_TEMPLATE.md` and `ISSUE_TEMPLATE/` are for humans and agents
opening PRs/issues against this repo — keep them free of anything that reads
as an instruction to an AI reviewer (this repo receives real automated
review traffic).
