### Removed

- **`project plan --gate-missing-required`/`--gate-unexpected-target` are
  gone, no CLI alias** — the aggregate gate policy those flags stamped onto
  `run-plan.json` is now durable project configuration instead of a
  per-invocation flag: `.abicheck.yml`'s new optional `aggregate: gate:`
  block (`missing_required`/`unexpected_target`) is sourced by
  `project plan` directly. `check-project.yml`'s matching
  `on-missing-required`/`on-unexpected-target` workflow-call inputs are
  removed too — set the policy in the calling project's `.abicheck.yml`.
  (CLI cleanup phase two, PR 2 follow-up.)
