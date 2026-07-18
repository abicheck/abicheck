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
| `security.yml` | Yes | CodeQL + related static checks |
| `ci.yml`'s `windows-msvc` job | No — `continue-on-error: true` | MSVC+PDB lane is still maturing; informational only |
| `ci.yml`'s `heavy-parity-gate` → `libabigail-parity`/`abicc-parity` | Conditional | Only runs when `abicheck/**`, `tests/**`, `examples/**`, or `.github/workflows/**` changed (path-filtered via `dorny/paths-filter`) |
| `clang-plugin.yml` | **No** | Standalone, path-filtered to `contrib/abicheck-clang-plugin/**`; never a required abicheck-CI gate (see `contrib/abicheck-clang-plugin/AGENTS.md`) |
| `mutation.yml` | No | Weekly / `mutation` label / dispatch |
| `performance.yml` | Partially | Runs on PRs touching detector-core files; see `docs/development/performance.md` |
| `examples-validation.yml` / `-nightly.yml` | No | Scheduled/manual regression sweeps over the example catalog |
| `eval-suite.yml` | No | Real-world regression guard, scheduled |
| `realworld-validation.yml` | No | conda-forge package validation harness |
| `agentready.yml` | No (informational) | Runs the external AgentReady structural scanner; posts SARIF/step-summary. Distinct from — and does not replace — `scripts/check_ai_readiness.py`, which enforces abicheck-specific invariants (ChangeKind partition, doc-count sync, import cycles, ...). See root `AGENTS.md`'s "AI-readiness gate" section. |
| `test-action.yml` | Yes, when `action/**`/`action.yml` changes | See `action/AGENTS.md` |
| `publish.yml` / `pages.yml` | N/A (release/deploy only) | Not PR gates |

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
