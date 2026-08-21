# CLAUDE.md — `agent-evals/skills/harbor/`

[Harbor](https://www.harborframework.com) (`harbor-framework/harbor`) is a
third-party, general-purpose agent-evaluation framework — the successor to
Terminal-Bench, from the same team. `tasks/` is a generated Harbor task
battery, one task per `status: ready` scenario in `agent-evals/skills/
scenarios.yaml`. This is **additive**, not a replacement: the existing
harness (`runners/claude_code.py`, `graders/`, `scenarios.yaml`,
`skill-eval-pack.json`) is unchanged and still the harness the committed
pilot (`pilot-results/README.md`) ran on. See ADR-058's Harbor-migration
amendment for the full decision record and what remains open.

## Why this exists

A review of the existing harness found it was a fully custom, in-repo
system with no established task-authoring/verification convention behind
it — no sandboxed task definition a reader could recognize, no verifier
separated from the harness's own Python, and no way to run these 12
scenarios through any tooling outside this repository. Harbor's own task
model (`task.toml` + `instruction.md` + `environment/` + `tests/` +
`solution/`) is exactly that convention, and its `claude-code` agent
adapter drives the real `claude` CLI natively — the same agent the existing
pilot used, verified against Harbor's own source (`harbor.agents.installed.
claude_code`), not assumed.

## What is real here, and what is not

**Real, and verified in this pass:**
- `scripts/gen_harbor_tasks.py` produces all 12 tasks, validated against the
  real `harbor` package's own `Task`/`TaskConfig` Pydantic models
  (`pip install harbor` into a throwaway venv, `harbor.models.task.task.
  Task(task_dir)` — not a hand-guessed schema).
- Every Category A scenario's `solution/solve.sh` was run **end to end**
  (real `gcc` compile, real `abicheck compare`, through the real recording
  shim, through the real `verify_run.py` grading bridge) and produces
  `reward=1` — see `tests/test_gen_harbor_tasks.py::
  TestSolveScriptsEndToEnd`. This is genuine grading-pipeline verification,
  not a structural check.
- `evidence.py`/`dimensions.py`/`claim.py` (the deterministic graders) are
  reused **unmodified** — `verify_run.py` is a thin bridge, not a second
  implementation.

**Not verified, and stated as such rather than implied otherwise:**
- **No task has been run through a real Harbor trial.** This sandbox has no
  Docker daemon (`dockerd` cannot start here — confirmed, not assumed), so
  `harbor check`/`harbor run` could not be exercised against these tasks.
  Everything Docker-dependent (the `environment/Dockerfile` actually
  building, the agent actually running inside the container, the verifier
  actually reading `/logs/verifier/`) is unverified beyond static review.
- **`harbor` is not a repository dependency.** It was installed into a
  throwaway venv (`/tmp/harbor-venv`, not committed, not in
  `pyproject.toml`) purely to validate this generator's output against the
  real schema. Adding it as a real dev/CI dependency, wiring `harbor run`
  into any CI job, or provisioning Docker-in-CI are all separate decisions
  nobody has made yet.
- **The A/B (skill vs. baseline) mechanism is a documented design, not a
  proven one.** `[environment].skills_dir` and the `--ak skills_dir=...`
  override are real, confirmed-from-source Harbor mechanisms, but no trial
  has actually exercised either path.
- **`dimension_1`'s skill-activation check has no Harbor equivalent.** A
  Harbor task has no "arm," so `verify_run.py` calls `grade_run(...,
  arm=None)` — activation-was-required is simply not checked here. See
  `verify_run.py`'s own docstring.

## Regenerating

```bash
python scripts/gen_harbor_tasks.py          # write
python scripts/gen_harbor_tasks.py --check  # verify (wired into verify.py's `harbor-tasks` step)
```

Regenerate after any change to `scenarios.yaml`, a fixture, `skill-eval-
pack.json`, or `runners/claude_code.py`'s `ANSWER_CONTRACT`/`strip_comments`/
`demo_app_sources`/`EXPLANATORY_FILES` (the generator imports these
directly, so a change there is a change here too, caught by `--check`).

## Running a real trial (untested in this repository — read the caveats above)

```bash
pip install harbor
# baseline (no skill):
harbor run -a claude-code -m claude-sonnet-5 \
  -c agent-evals/skills/harbor/tasks/removed-export
# skill arm:
harbor run -a claude-code -m claude-sonnet-5 --ak skills_dir=/opt/skills \
  -c agent-evals/skills/harbor/tasks/removed-export
```

## What NOT to do

- Don't hand-edit anything under `tasks/` — it is entirely generated; edit
  the generator (`scripts/gen_harbor_tasks.py`) or its inputs instead.
- Don't add ground truth (`tests/scenario.json`'s content) to
  `environment/` — the agent's own workspace. That is the one invariant
  `tests/test_gen_harbor_tasks.py::TestTaskStructure::
  test_scenario_json_is_scoped_to_tests_not_environment` exists to pin.
- Don't remove or start deprecating the existing `runners/claude_code.py`
  harness on the strength of this directory existing — that decision is
  explicitly not made by this migration; see the ADR amendment.
