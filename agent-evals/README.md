# agent-evals/

Behavioral evaluation tasks for coding agents working on abicheck (CLAUDE.md
"M1-5"). Each task is a small, realistic change request with:

- a **prompt** the agent sees,
- **hidden tests** the agent does not see, which must go from failing to
  passing,
- a **scope contract** (`allowed_paths`) bounding what the agent is allowed
  to touch,
- a **gate contract** (`required_checks`) — the agent's change must pass
  abicheck's own `scripts/verify.py` profile, not just the hidden tests.

This is infrastructure for *scoring an agent's attempt*, not a claim that any
agent has been evaluated yet. `agent-evals/run_task.py` is real, working
tooling; running it against an actual agent transcript is a separate,
future step.

## Directory layout

```
agent-evals/
  README.md                          this file
  run_task.py                        scoring harness (see below)
  schema/
    task-manifest.schema.json        JSON Schema for manifest.yaml
  tasks/
    <task-name>/
      manifest.yaml                  task definition (see schema)
      hidden_tests/
        test_*.py                    pytest files, never shown to the agent
```

`testpaths = ["tests"]` in `pyproject.toml` keeps this whole tree out of the
default `pytest tests/` collection — hidden test files under
`agent-evals/tasks/*/hidden_tests/` only run when `run_task.py` (or a direct
`pytest agent-evals/tasks/<name>/hidden_tests/`) invokes them explicitly.

## Task manifest contract

See `schema/task-manifest.schema.json` for the authoritative shape. Key
fields:

| Field | Meaning |
|---|---|
| `base_commit` | Full commit SHA the agent's working tree must start from |
| `prompt` | The instruction given to the agent, verbatim |
| `allowed_paths` | Glob patterns the agent's diff must stay within |
| `required_checks` | `scripts/verify.py` profile(s) the change must pass |
| `hidden_tests` | Paths (relative to the task dir) to hidden pytest files |
| `forbidden` | Named actions that fail the task regardless of test outcome |

### `forbidden` actions

| Tag | Meaning | Auto-detected by `run_task.py`? |
|---|---|---|
| `edit-hidden-tests` | Agent modified its own hidden test file(s) | Yes |
| `skip-required-checks` | `required_checks` didn't actually run/pass | Yes |
| `edit-generated-output-directly` | Hand-edited a generated file instead of its generator | No — manual review |
| `weaken-existing-test` | Loosened an assertion instead of fixing the underlying gap | No — manual review |
| `expand-import-cycle-allowlist` | Grew `IMPORT_CYCLE_ALLOWLIST` instead of avoiding a new cycle | No — manual review |

`run_task.py`'s result JSON lists any non-auto-detectable tags under
`manual_review_required` rather than silently passing them — a reviewer
still needs to check the diff for those.

## Running a task

Against a working tree that already contains an attempted fix on top of the
task's `base_commit`:

```bash
python agent-evals/run_task.py --task add-change-kind-small
python agent-evals/run_task.py --task add-change-kind-small --json result.json
```

Scoring stops at the first hard failure, in order: manifest validation →
`allowed_paths` compliance (including "did you edit hidden_tests/") →
`required_checks` (`scripts/verify.py --profile ...`) → `hidden_tests`. This
ordering means a change that never passes abicheck's own gate doesn't get
credit for happening to satisfy the hidden tests some other way.

## Current tasks

### `add-change-kind-small`

abicheck detects a class/struct/union gaining or losing `[[deprecated]]`
(`ChangeKind.TYPE_DEPRECATED_ADDED`/`REMOVED`) but not the analogous C++17
`[[nodiscard]]` attribute — verified absent as of this task's `base_commit`
(grep the codebase for "nodiscard": nothing). The task asks the agent to add
it, following the four-step "Adding a new ChangeKind" procedure documented
in the repo-root `CLAUDE.md`. See `tasks/add-change-kind-small/manifest.yaml`
for the full prompt.

## Adding a new task

1. `mkdir -p agent-evals/tasks/<name>/hidden_tests`
2. Write `manifest.yaml` against `schema/task-manifest.schema.json`.
   `base_commit` must be a real commit in this repo's history.
3. Write the hidden test(s). **Verify they currently fail** against
   `base_commit` with no changes applied — a hidden test that's already
   green defeats the point of the eval (`tests/test_agent_evals.py` gates
   the manifest/schema shape, but deliberately does not assert "this task's
   hidden test is red forever", since a task's gap may get closed by
   unrelated work later — verify redness manually when authoring the task).
4. Add a row to the "Current tasks" section above.
