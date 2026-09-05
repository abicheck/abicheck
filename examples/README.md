# `examples/` — workflow examples

This directory holds curated, task-oriented walkthroughs — start here if you
want to *use* abicheck. The calibration corpus (197 `caseNN_*` compatibility
fixtures) lives in a separate tree, [`../catalog/`](../catalog/README.md) —
see that README for the encyclopedia of ABI pitfalls the benchmark and CI
gates run against.

## Workflow examples — start here

`workflows/<task>/` is a small, curated, task-oriented walkthrough: a tiny
purpose-built project, the real `abicheck` command you would run, and what
the output means.

| Workflow | Task |
|---|---|
| [`workflows/compare-release/`](workflows/compare-release/README.md) | Did my next release break anything for existing consumers? |

Each one carries a `workflow.yaml` stating the commands, the expected exit
code, and the expected verdict; `validation/scripts/run_workflow_examples.py`
executes exactly those documented commands in CI, so a walkthrough cannot rot
into something that no longer works. See
[`docs/contribute/catalog-coverage.md`](../docs/contribute/catalog-coverage.md)
for progress against the full planned set.

## The calibration catalog

Everything under `../catalog/` is calibration material: one case per
compatibility mechanism, driving the FP-rate, tier-accuracy, mutation, and
full-catalog gates. It is an encyclopedia of ABI pitfalls, not a tutorial —
the published, navigable version is the
[Compatibility Catalog](../docs/reference/examples/index.md), which indexes
the same cases by rule, scenario kind, ecosystem, operation, evidence level,
language, and verdict. See [`../catalog/README.md`](../catalog/README.md)
for the headline counts, verdict distribution, and full case index.
