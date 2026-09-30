# `set-up-abi-compatibility-ci` evaluation

Measures whether the [`set-up-abi-compatibility-ci`](../../../set-up-abi-compatibility-ci/SKILL.md)
skill makes a coding agent produce a *correct* GitHub Actions integration of
abicheck, compared with the same agent without the skill.

This is a separate harness from `../skills/` (G37) on purpose: G37 grades a
compatibility **verdict** claim; this skill's outcome is a **configuration**
(workflow files), so it is graded by inspecting the files an agent wrote.

## Layout

| Path | Role |
|---|---|
| `fixtures/<name>/` | Ten small GitHub-shaped repositories: CMake C, CMake C++ with a static default, Makefile with two libraries, an existing broken `mode: scan` workflow, Meson with fork contributors, a declared RHEL 8 glibc floor, an inline/template-heavy C++ API (source depth), Autotools/libtool, Bazel, and an aarch64 cross-compiled library. |
| `scenarios.yaml` | Fixture + the request a maintainer would type (one in Russian) + the checks that apply, and which checks are zero-tolerance (`critical`). |
| `grader.py` | Deterministic checks over the resulting `.github/workflows/*.yml` and the agent's final message. No model is called. |
| `reference/<scenario>/` | Hand-written good workflows; the grader's positive oracle. |
| `run_eval.py` | Two-arm runner: materializes each fixture as a fresh git repo (commits, tags, `origin` remote) outside this checkout, installs the skill into `.claude/skills/` for the `skill` arm only, runs `claude -p`, grades. |
| `token_report.py` | Where a run's money goes: cache reads vs cache writes vs output, turns, which skill files the agent read, and cost per *successful* run. `--skill-dir` on `run_eval.py` A/Bs two versions of the skill. |
| `results/` | Committed evidence from real runs — start with [`2026-09-30.md`](results/2026-09-30.md): across ten scenarios the skill arm had no critical failure in every run; without it, 1/30. |

`tests/test_ci_setup_skill_eval.py` pins the grader's contract: every
reference passes every check, an untouched fixture never succeeds, and each
failure mode from the skill's `references/pitfalls.md`, injected into a good
workflow, is caught by the check named for it.

## What the checks encode

| Check | Failure mode it catches |
|---|---|
| `abicheck_workflow_on_pr` | no compare step on `pull_request` at all |
| `pinned_abicheck` | `@main` / floating refs |
| `no_pull_request_target`, `permissions_declared`, `contents_write_not_on_pr` | unsafe token scope |
| `no_retired_inputs`, `no_continue_on_error` | a check that errors out or can never fail |
| `not_self_compare`, `baseline_meaningful` | comparing a build with itself or with a placeholder |
| `baseline_release`, `release_bootstrap` | a release baseline that is never published, misnamed (not `*.abicheck.json`), or has no bootstrap for the first PR |
| `baseline_without_releases` | `latest-release` in a repository that has no releases |
| `no_ambiguous_latest_release` | `latest-release` with several libraries' assets |
| `headers_public` | private (`src/`), whole-repo, or missing headers |
| `lang` | a C library parsed as C++ (the Action default) |
| `covers_libs` | only one of several shipped libraries checked |
| `shared_build` | a static-by-default library checked without a shared build |
| `toolchain_via_action` | distribution CastXML, below abicheck's supported range |
| `debug_info` | check build without debug info (scored, not critical) |
| `runtime_floors_declared`, `runner_pinned` | a stated glibc floor never declared (floor raises stay exit-0 warnings); a floating runner image |
| `source_depth_complete`, `baseline_has_source_evidence` | L4 requested without clang/build evidence, or only on the new side |
| `cross_toolchain` | a cross-compiled library's headers parsed with the host compiler |
| `library_path_plausible` | pointing at a path the build system never writes (`.libs/`, `bazel-bin/`) |
| `pins_are_commits` | a SHA pin to an annotated tag object, which `uses:` cannot run |
| `single_abi_workflow` | a second workflow added next to a broken one instead of repairing it |
| `report_mentions` | the summary omits a fact the user needs (fork-PR limitation, why the old check never failed) |

## Running

```bash
python scripts/gen_agent_skills.py        # the runner installs skills/<name>
python skills-src/evaluation/agents/ci-setup/run_eval.py \
    --out /tmp/ci-setup-eval --repetitions 3 --jobs 6 --model claude-sonnet-5-5 \
    --abicheck-venv /opt/abicheck-eval-venv \
    --hide "$LAUNCHING_SESSION_SCRATCH_DIR"   # isolation: see below
python skills-src/evaluation/agents/ci-setup/run_eval.py --out /tmp/ci-setup-eval --report-only
```

`--out` must be outside the checkout. **Use `--abicheck-venv`** (a venv with
abicheck installed from a wheel, not editable): it runs each agent in a
private mount namespace (`unshare --mount`, Linux, root) where this checkout,
other runs' outputs, and the parent session's transcripts are hidden. Without
it an agent can `find /` its way into abicheck's own sources — observed in a
real run, see `results/2026-09-30.md`. Both arms get identical tools,
including web access, so the baseline can read abicheck's public
documentation the way an unequipped agent in a real repository would.

## Limits

- Static grading only: no workflow is executed on GitHub. A workflow can pass
  every check and still fail at runtime for a reason no check models.
- One model, one agent (headless Claude Code). Not cross-agent evidence.
- The scenario set was written together with the skill; the skill was revised
  after a single smoke run (see the results file). Not a held-out result.
