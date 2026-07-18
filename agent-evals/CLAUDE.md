# CLAUDE.md — `agent-evals/`

Behavioral coding-agent evaluation suite (CLAUDE.md root "M1-5") — scores an
agent's attempt at a small, realistic abicheck change request against hidden
tests. See `README.md` in this directory for the full contract (manifest
schema, scoring stages, `forbidden` actions); this file is the short agent
orientation, not a duplicate of it.

## Module map

| Path | Role |
|------|------|
| `run_task.py` | Scoring harness. Validates a task's manifest, checks the working-tree diff stays within `allowed_paths`, runs `required_checks` via `scripts/verify.py`, then runs `hidden_tests` via pytest. Emits JSON. |
| `schema/task-manifest.schema.json` | JSON Schema every `tasks/*/manifest.yaml` must validate against. |
| `tasks/<name>/manifest.yaml` | One task definition: prompt, scope, gates, hidden tests. |
| `tasks/<name>/hidden_tests/*.py` | Pytest files never shown to the agent under evaluation. |

## What NOT to do

- Don't add `agent-evals/` to `pyproject.toml`'s `testpaths` — hidden tests
  must stay out of the default `pytest tests/` collection (see
  `tests/test_agent_evals.py`, which verifies the schema/manifest shape
  without ever asserting a task's hidden test is red forever).
- Don't hand-edit a task's `manifest.yaml` `base_commit` to "make an in-flight
  attempt pass" — regenerate the task from a real commit instead.
- Don't assume `run_task.py` detects every `forbidden` action in a
  manifest — some (`weaken-existing-test`, `expand-import-cycle-allowlist`,
  `edit-generated-output-directly`) need human diff review; the runner
  surfaces those under `manual_review_required` rather than silently passing
  them.
