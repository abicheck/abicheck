# AGENTS.md — `.github/`

CI/CD workflows, the composite Action's manifest, issue/PR templates, and
review ownership. See the repository root `/AGENTS.md` for the canonical
project-wide contract — this file only covers what's specific to this tree.

## Required vs. informational workflows

Not every workflow here blocks a merge — and, per the decision below, as of
2026-09 **none of them mechanically can**: `main` carries no
`required_status_checks` Ruleset rule, so GitHub's merge button is never
disabled by CI state. The table's "Required?" column is retained as
*guidance* — which checks a human (or reviewing agent) should treat as
"fix this before merging" — not as a description of an enforced gate.
Before assuming a red check means "fix this before merging," check which
bucket it's in:

| Workflow | Required on every PR? | Notes |
|----------|------------------------|-------|
| `ci.yml` | **Yes** — `ai-readiness`, `fair-metadata`, `lint-and-types`, `unit-tests` (canonical Linux/3.13 lane), `packaging` jobs | The core gate. `unit-tests`' `integration-tests`/`windows-msvc` sibling jobs in the same workflow have their own rules below. `ai-readiness` also runs the ADR-061 bounded-module architecture gate as its own step (`scripts/verify.py --profile pr --only architecture`, i.e. `scripts/check_architecture.py`) — there is no separate `module-architecture.yml` workflow or check. |
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

## Required-status-check configuration — deliberately not enforced (2026-09)

**Decision: `main` does not require status checks to pass before a merge,
and this is intentional, not an oversight or an outstanding admin TODO.**

CLI cleanup phase two's PR A / PR 0B originally built toward the opposite —
a real GitHub required-status-checks Ruleset that would block the merge
button until every check in a derived 14-name list passed on the PR's head
SHA, plus `verify-merge-checks.yml` as a post-merge audit to catch a merge
that slipped through before that Ruleset was actually applied. Both pieces
were built, tested, and the Ruleset was eventually applied by an admin. It
worked as designed: merges to `main` were blocked until CI finished.

The maintainer then decided that trade-off isn't wanted going forward —
waiting for CI to complete before a push/merge can land is a real cost to
iteration speed, and this repo chooses to accept the risk of an occasional
red or incomplete merge over paying it on every merge. Concretely:

- The Ruleset's `required_status_checks` rule has been removed.
  `.github/branch-protection-ruleset.json` now carries only a
  `non_fast_forward` rule (no force-pushes/history rewrites on `main`) —
  see `.github/branch-protection-ruleset.md` for the current runbook.
- `.github/workflows/verify-merge-checks.yml` has been **removed outright**,
  along with its dedicated tests (`tests/test_verify_merge_checks_race_logic.py`,
  `tests/verify_merge_checks_harness.mjs`). That workflow's entire purpose
  was catching a merge whose required checks hadn't actually finished before
  `merged_at` — the ADR-style problem statement was "a required check that
  didn't block a merge is a detectable gap, not a policy". Once the policy
  itself became "don't require checks to block a merge," every merge it
  used to flag as a finding became expected, normal behavior instead — an
  audit that fires on every single merge, correctly, is not an audit
  anymore, it's noise. There is no compensating mechanism to re-add in its
  place; the absence of merge-blocking is the accepted state, not a gap.
- The rest of this file's "Required vs. informational workflows" table is
  kept as-is and still means what it says as *review guidance* — which
  checks a human or reviewing agent should treat as "fix this before
  merging" — it just no longer describes anything GitHub itself enforces
  mechanically on the merge button.

**If this decision is ever reversed**, the mechanical pieces this repo built
for it are still intact and don't need to be reinvented — see the
now-historical "PR 0 — restore a green CI baseline first" section of
`docs/contribute/plans/cli-cleanup-phase-two.md` for the full original
design (the required-check derivation rule, the `docs-pr (required)`/
`test-action (required)` neutral-aggregate gate jobs in `ci.yml` that make a
path-filtered workflow requirable without stranding unrelated PRs, and the
one-stable-aggregate-check-per-workflow principle). Re-deriving the
required-check list from this file's "Required vs. informational workflows"
table, adding a `required_status_checks` rule with that list to
`branch-protection-ruleset.json`, and applying it is enough on its own —
re-adding a `verify-merge-checks.yml`-style post-merge audit is optional at
that point (it only earns its keep while the Ruleset's enforcement itself is
still being rolled out or is unverified, per its original design rationale),
not a required companion piece.

## Local equivalence (CLAUDE.md "M0-3")

`ci.yml`'s always-required jobs (`ai-readiness` — which includes the
ADR-061 architecture step — `fair-metadata`, `lint-and-types`, the
canonical `unit-tests` Linux/3.13 lane, and `packaging`, reproduced by the
`pr` profile's own `distribution-build` step) are reproducible through
`python scripts/verify.py --profile pr` — `tests/test_verify_profiles.py`
asserts the core `ci.yml` catalog stays in sync. **Don't add a new required
check without adding the matching `Step` to `scripts/verify.py`'s catalog** —
an agent that only runs the local `pr` profile and gets a clean result should
never be surprised by a required CI job it had no way to reproduce.

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
