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
| `ci.yml` | **Yes** — `ai-readiness`, `fair-metadata`, `lint-and-types`, `unit-tests` (canonical Linux/3.13 lane, three `--shard=K/3` jobs) + `unit-tests-coverage` (combines their coverage data and enforces the 95% floor once), `unit-tests-other-os` (macOS/Windows, no coverage; runs on `main` pushes, dispatch, and PRs labelled `ci:full` — add the label to a platform-sensitive PR), `slow-tests`, `packaging` (Windows; the Linux build is `fair-metadata`'s `distribution-build`) jobs | The core gate. Tests marked `repo_scan` (whole-tree structural scans, OS- and coverage-independent) are excluded from every unit leg and run once, as a step of `ai-readiness` (`python scripts/verify.py --profile pr --only repo-scan-tests`) — it was its own `repo-scan-tests` job until 2026-10, folded in because both jobs had the identical Python 3.13 + `[dev]` setup and neither is on the critical path. `lint-and-types` is the one PR-time docs build owner (`docs-build` + `examples-docs`, installing the `[docs]` extra). The real-toolchain lanes (integration, native PE/Mach-O compare, MSVC+PDB, libabigail/ABICC parity) live in `integration.yml`, below. `ai-readiness` also runs the ADR-061 bounded-module architecture gate as its own step (`scripts/verify.py --profile pr --only architecture`, i.e. `scripts/check_architecture.py`) — there is no separate `module-architecture.yml` workflow or check. `slow-tests` is the `slow` marker lane, split out of `unit-tests`' canonical leg (where it ran as a step behind the ~23-minute main suite) so it runs concurrently instead — still required on every PR, still every `slow`-marked test, reproducible locally as `python scripts/verify.py --profile full --only slow,slow-perf`. |
| `changelog-check.yml` | Yes, only when the diff touches `abicheck/**/*.py` | Bypass with the `skip-changelog` label |
| `cli-interface-check.yml` | Yes, when the CLI surface changes | Diffs `dump_cli_surface.py` output old vs. new |
| `dependency-review.yml` | Yes | GitHub's built-in dependency-review action. PR-only, path-filtered to dependency manifests (`pyproject.toml`, `requirements*.txt`, `pixi.lock`, `action.yml`, workflows) — it diffs those only. Complements, does not duplicate, `security.yml`'s pip-audit (whole resolved tree, every advisory). |
| `docs-review-triggers.yml` | No (informational) | Diffs the PR's changed files against every docs page's front-matter `depends_on` list (`scripts/check_docs_review_triggers.py`) and posts an `::notice::`/step-summary when they overlap — a nudge to re-check that page, never a merge blocker. |
| `security.yml` | Yes | One job (`Security Scan & CodeQL`): CodeQL (python, `build-mode: none`, `.github/codeql/codeql-config.yml` ignores `tests/`/`examples/`/`docs/`/`catalog/`), then pip-audit + bandit. PRs path-filtered to non-test `*.py`, `pyproject.toml`, this file and the CodeQL config; push to main and a Sunday 03:37 UTC cron run unfiltered. |
| `python-compat.yml` | **Yes** for 3.11/3.12/3.14; 3.15 (prerelease) and `free-threading` (3.15t) are `continue-on-error` and run on `main`/schedule/dispatch and `ci:full`-labelled PRs only | Tiered Python coverage: every non-canonical interpreter builds the sdist+wheel, installs the wheel into a clean venv, imports every module, smokes the CLI, and runs a small unit-test subset. The `free-threading` job runs the free-threaded 3.15t interpreter (conda-forge, the base no-GIL version this project tests; 3.14t is deliberately skipped), fails if any import re-enables the GIL, and runs the concurrency test subset with `PYTHON_GIL=0`. The full suite runs only on `ci.yml`'s canonical 3.13 `unit-tests`. |
| `integration.yml` — one `integration (<os>)` job per OS (`ubuntu-24.04`, `macos-latest`, `windows-latest`) | Conditional on PRs; always on `main` pushes and dispatch | Every lane is a `scripts/verify.py --profile full` step, run as a step of its OS's job: `integration` on all three (xdist via `PYTEST_ADDOPTS`; Linux adds coverage for the Codecov `integration` flag and raises the executed-test floor to 20), `native-compare` (macOS clang / Windows MinGW, floor 5), `msvc` (Windows, step-level `continue-on-error` — the PDB parser is still maturing, informational only), `libabigail-parity` and `abicc-parity` (Linux). Later test steps run whatever an earlier one concluded, so one red lane never hides another. PRs trigger it only when one of `abicheck/**`, `tests/**`, `examples/**`, `catalog/**`, `.github/workflows/**`, `pyproject.toml`, `action/**`, `.github/actions/**`, or `scripts/verify.py` changed — a trigger-level `paths:` filter, not a gate job. The last four paths are the lanes' own *infrastructure* (they pip-install the package, set CastXML up via the composite action, and run `verify.py` steps), so omitting them let a re-pin of CastXML merge without a single CastXML-using job running. This replaced nine `ci.yml` runners (`heavy-parity-gate`, `integration-tests` x3, `cross-platform-e2e` x2, `windows-msvc`, `libabigail-parity`, `abicc-parity`); the gate existed to keep a stable required check, which stopped mattering once no check was merge-blocking. Every step passes `--require-complete`, so a step whose precondition fails (a tool the leg installs went missing) fails instead of exiting 0 as a skip. The Linux leg also runs `demo-libz` (formerly `ci.yml`'s `e2e` job). |
| `clang-plugin.yml` | **No** | Standalone, path-filtered to `contrib/abicheck-clang-plugin/**`; never a required abicheck-CI gate (see `contrib/abicheck-clang-plugin/AGENTS.md`) |
| `mutation.yml` | No | **Auto-runs on any PR touching a mutated module** (path-filtered to `[tool.mutmut].only_mutate` **and each module's own `tests/test_<stem>*.py`** — a naming-convention match, not a complete one: 257 of ~450 test files import a mutated module directly, so a complete trigger is effectively `tests/**`, i.e. a two-hour job on most PRs; the weekly run and the `mutation` label stay the complete checks — a PR that only weakens a detector test changes no production file, so the source-path entries alone never started the lane; both halves kept in sync by `tests/test_mutation_workflow_contract.py`), gating `--diff-scoped`: a surviving mutant in a function the branch changed fails, with no baseline needed. A test-only diff has no changed function to scope to, so such a run says it gated nothing rather than printing OK — that case is only checkable as drift, and when the trigger was a *detector test* — or the `mutation` label, which is documented as the complete check — the PR lane passes `--require-baseline` so it fails closed instead of going green on a run that checked nothing. Recording `mutation-baseline.json` once (dispatch, `write_baseline: true`) is what turns that failure into a real gate. Also weekly (per-module baseline drift, `--require-baseline`) and on dispatch (record the baseline). The `mutation` label still forces a run on a PR the path filter misses. **Run cost:** with no baseline recorded, the PR run executes only the mutants of the `only_mutate` functions the branch changed (`--scope-run-to-functions`; without a baseline the diff-scoped gate is the only reader of any mutant's outcome, so nothing it reads is dropped — and touching a changelog fragment or test no longer forces a full run). A `--require-baseline` run fails before invoking mutmut when no baseline exists, instead of after the full run. Any run that must measure the whole population is split across 4 matrix shards (`--shard K/4`, a size-balanced partition of `only_mutate`), rolled up under the single `mutmut (detector core)` check; a dispatch baseline recording merges the per-shard parts (`scripts/mutation_scope.py merge-baselines`, which refuses parts that do not partition `only_mutate`). `pyproject.toml` starts the lane only when `[tool.mutmut]`/`[tool.pytest]` changed (`mutation_scope.py pyproject-changed`), not on a dependency pin. |
| `reference-mode.yml` | No | **H5 under the production `ABICHECK_REFERENCE_MODE=1` switch** (design-hardening plan Phase 4): weekly, on dispatch, and on a PR labelled `reference-mode`. Runs the whole H5 harness (`tests/test_family_f5_optimization_reference.py`, every marker -- the binary, CastXML-header and environment-level cells included, so it installs CastXML) plus the H7 replay of the F5 mutants, three of which disable the switch in the cache wrapper, the thread budget and the disk-cache policy. `ABICHECK_MIN_EXECUTED` keeps a lane whose compiler or CastXML failed to install from passing on skips. Add the label to a PR that adds or changes a cache or pool. |
| `performance.yml` | Partially | Runs on PRs touching detector-core files; see `docs/contribute/performance.md` |
| `usecase-paths.yml` | No (informational) | Records which `abicheck` functions each automated scenario runs, on the PR base and head (`scripts/usecase_paths.py`), and writes the difference to the step summary: runs whose path changed, and functions no use case reaches any more. Weekly/dispatch adds the real-binary flows (`scripts/usecase_flows.yaml`) and publishes the importance ranking and the dead-code candidate list (`usecase_paths.py dead`). |
| `examples-validation.yml` | No | Per-PR/main (path-filtered) example-catalog validation: three toolchain legs (`examples-gcc`: CLI debug-headers + runtime smoke + build-source proof + owner/bundle/special-CLI lanes; `examples-clang`: clang Python-API + CLI + clang header fallback + workflow walkthroughs; `examples-reduced-evidence`: release/stripped FP guard) and a toolchain-free `full-example-matrix` collector. The gcc Python-API pipeline is owned by `ci.yml` `integration-tests` |
| `examples-validation-nightly.yml` | No | Scheduled/manual regression sweep over the example catalog |
| `eval-suite.yml` | No | **Claim: abicheck's own verdict on ~22 real conda-forge release pairs has not drifted** (`skills-src/evaluation/field/runner.py`, `manifest.yaml`'s `expect` is the last accepted verdict, not ground truth — contrast `real-library-corpus.yml`). Binary tier = one `.so` per package, `--fail-on-drift`, which prints each drifting row's finding kinds. Source tier = clone, configure and build the manifest's library `target` for the few entries with a `source:` block, then dump the built `.so` with its `public_headers` and the target's own compile DB at `--depth source`; `--fail-on-empty-source` fails only when no row captured complete L3/L4/L5 evidence on both sides. Weekly, dispatch (`tier`/`only` inputs), and PRs labelled `eval`. |
| `real-library-corpus.yml` | No | **Claim: abicheck's verdict agrees with *cited* ground truth on real conda-forge release pairs** (H6/F6, `skills-src/evaluation/validation/scripts/run_compat_corpus.py` over `data/compat_corpus.json`): every shared object both builds ship, compatible ⇒ no BREAKING/API_BREAK, incompatible ⇒ a BREAKING finding plus every documented kind, and per-kind non-breaking count drift against `data/compat_corpus_baseline.json` (report-only until recorded). **One job per corpus `library`** (`--library`, matrix pinned to the corpus file by `tests/test_real_library_corpus_matrix.py`), each `abicheck compare` bounded by `--compare-timeout` (45 min) so one slow library is recorded as "not evaluated" instead of starving the rest: the first header-aware serial run spent 93 min on one protobuf pair and was cancelled with six pairs never started. Weekly, dispatch, and PRs labelled `real-corpus`. |
| `realworld-validation.yml` | No (`continue-on-error`) | **Claim: the package/bundle path works on a real distro package.** Downloads Ubuntu's `zlib1g`/`zlib1g-dev` `.deb`s and self-compares them as packages (with and without `--contract public`, asserting a clean verdict, empty `bundle_findings` and a zero coverage exit contribution), plus an `abidiff` cross-check and the package/bundle unit tests. Path-filtered to the package/bundle/contract owners it exercises, weekly, dispatch. |
| `integration.yml`'s `End-to-end demo (libz)` step (Linux leg) | Conditional (that workflow's `paths:`, which includes `scripts/demo_libz.py`) | **Claim: one real system library dumps with its real headers and its synthetic mutations classify correctly** (`python scripts/verify.py --profile full --only demo-libz --require-complete`, i.e. `scripts/demo_libz.py`). Formerly `ci.yml`'s always-on `e2e` job: the demo takes ~3 s, and as its own job it paid a full runner's apt/CastXML/pip setup on every PR. |
| `agentready.yml` | No (informational) | Runs the external AgentReady structural scanner: PR diff (PR comment + SARIF) and a `scan` on push to main; no cron (pinned tool, push already scans every main commit). Distinct from — and does not replace — `scripts/check_ai_readiness.py`, which enforces abicheck-specific invariants (ChangeKind partition, doc-count sync, import cycles, ...). See root `AGENTS.md`'s "AI-readiness gate" section. |
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
design (the required-check derivation rule and the
one-stable-aggregate-check-per-workflow principle). The `docs-pr (required)`/
`test-action (required)` bridge jobs that design put in `ci.yml` were
**deleted** (2026-09-30): with nothing required they only polled another
workflow's check for up to 25/35 minutes each, holding a runner on every PR.
Re-enabling merge-blocking must bridge a path-filtered workflow with a
reusable-workflow call and ordinary `needs:` instead (plan
`docs/contribute/plans/ci-cost-and-assurance.md`, Phase 1) —
`tests/test_required_checks_governance.py` rejects a sleep-loop poller. Re-deriving the
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
- **Installing apt packages goes through
  `./.github/actions/install-system-deps`**, never a hand-written
  `sudo apt-get update && sudo apt-get install`. `apt-get update` fails as a
  *whole* when any configured source fails, and GitHub's runner images ship
  third-party vendor repositories (dl.google.com/linux/chrome-stable,
  packages.microsoft.com) that nothing in this repo installs from -- so an
  `update && install` chain aborts before `install` runs, and a Chrome index
  serving a stale `Packages.gz` takes out lanes that only wanted gcc. The
  shared action makes `update` advisory and `install` the gate, bounds and
  retries each attempt, and verifies afterwards that the packages really are
  installed. `tests/test_apt_install_hardening.py` executes that behaviour
  against a simulated apt and asserts the invariant over every workflow, so
  a re-hand-rolled `update && install` fails CI. A job that must add its own
  repository first (`clang-plugin.yml`) keeps its inline `apt-get`, but
  still absorbs `update`'s exit status with `||`.
- A job that checks the repo out into a subdirectory (`base/`, `head/`) has
  no repo at the workspace root for a `uses: ./...` path to resolve against;
  run the shared script directly instead (`bash
  head/.github/actions/install-system-deps/install.sh` with
  `INPUT_PACKAGES`), the same way those jobs already run
  `head/action/install-castxml.sh`.
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

## Product invariants for CI integration

Local consequences of root `AGENTS.md`'s "Product decisions and change
routing" section for anything that runs abicheck from a workflow:

- **Shared semantics.** Equivalent *resolved* requests produce the same
  decision whether they came from the Action, a reusable workflow, the
  CLI, or the Python API; a workflow may add convenience (baseline
  resolution, PR comments, SARIF upload), never a second gate algorithm.
  Raw-input resolution still differs by front end today — the CLI and
  Action discover `.abicheck.yml` and apply `--profile`/`--pack`, a bare
  typed-API call does not — and full configuration-resolution parity is
  direction, not a shipped guarantee.
- **Partial scope is normal.** A matrix cell or a local run checks its own
  selected target/profile against the matching baseline member; other
  variants are out of scope, and an expected-but-missing artifact is an
  incompleteness signal (warn by default, block by configuration), never a
  fabricated removal.
- **Trusted baseline selection.** A baseline is chosen by identity and
  coordinates (`channel × target × profile`, digests), never by a moving
  "latest" without recording the exact resolved artifact.
- **Prebuilt consumers are inputs, not builds.** A consumer artifact
  supplied to a check is used as-is for static inspection; rebuilding or
  executing it is a separately designed, explicitly opted-in validation
  mode (ADR-060 remains deferred).
- **Structured outcomes only.** New decision semantics travel through the
  typed report/`ExitDecision` fields and Action outputs; never scrape log
  text to derive a verdict or a gate.

This section does not change the repository's own merge policy above,
which stays as recorded.
