# CLAUDE.md — `examples/`

This directory is the **curated, task-oriented tree** — small workflow
walkthroughs a new user runs end to end, per audience (see
[the examples/catalog split's corrected Phase 4 target model]
(../docs/contribute/plans/examples-catalog-split.md)). It is not the
calibration corpus: the 197 `caseNN_*` fixtures the FP-rate, tier-accuracy,
mutation, and full-catalog-coverage gates run against now live under
`catalog/cases/` — see [`../catalog/CLAUDE.md`](../catalog/CLAUDE.md) for
everything about that tree (owner families, per-case layout, the README
template, ground truth, taxonomy, and how to add a case).

Read `README.md` in this directory first — it points at the curated
workflows below and at the calibration catalog's own
[`catalog/README.md`](../catalog/README.md).

## `workflows/` — curated user-facing examples (Phase 5, in progress)

`examples/workflows/` is a **separate tree from the `caseNN_*` calibration
catalog** under `catalog/cases/` — Phase 5 of the [examples/catalog split]
(../docs/contribute/plans/examples-catalog-split.md). Its entries are
small, complete, task-oriented projects a new user runs end-to-end
(`cd examples/workflows/<name>`, build, run one `abicheck` command, read
the output) — not calibration fixtures a gate scores. Consequently:

- **No `ground_truth.json` entry, no `caseNN` prefix, no taxonomy
  classification** — the "examples-ground-truth" AI-readiness check and
  every other gate that walks `catalog/cases/case*` directories
  deliberately never sees this tree (it filters on the `case` name prefix
  under `catalog/cases/`, not `examples/`).
- **Every workflow directory must carry a `workflow.yaml`** — the
  executable contract (schema in `scripts/workflow_examples.py`) naming its
  commands, expected exit code, expected output substrings, and expected
  verdict/change kinds. A directory without one is a hard error, not a
  free point of workflow coverage; it used to be exactly that, when the
  coverage report counted subdirectories.
- **A `run:` command runs with no shell** (`shlex.split`, `shell=False`);
  a pipe, redirect, `&&`, glob or variable is rejected at manifest load
  rather than handed to the program as a literal argument. Keep a
  documented command a single program invocation.
- **Every `run:` command in that manifest must appear verbatim in the
  workflow's own README** (whitespace-normalized). This is the rule that
  keeps the contract from becoming a second copy of the walkthrough, able
  to pass forever against commands the README no longer shows. Enforced by
  `workflow_examples.readme_drift`, in the fast lane
  (`tests/test_workflow_examples.py`) and again in the runner.
- Verify every command and every excerpted output block against a real run
  before writing it down — `validation/scripts/run_workflow_examples.py`
  does exactly that in CI (scratch copy, real shell, real `abicheck`), and
  the `workflow-examples` job in `examples-validation.yml` gates it. See
  `compare-release/README.md` + `compare-release/workflow.yaml` for the
  pattern.
- Link out to the relevant `docs/use/*.md`/`docs/learn/*.md` page for
  anything beyond that one task — a workflow example teaches "how do I run
  this", not "how does this work" (that's the docs' job, see
  `docs/AGENTS.md`'s ownership split).

See the plan doc's Phase 5 row for the target set (compare one library
[done], audit a release, multi-library project, evidence depth,
build/source evidence, Python API, suppressions, GitHub Actions) and which
of them remain.
