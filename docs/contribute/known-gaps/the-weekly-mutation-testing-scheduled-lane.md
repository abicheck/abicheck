---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The weekly `Mutation testing` scheduled lane (`.github/workflows/mutation.yml`, job `mutmut (detector core)`) can outgrow its own job timeout before producing a receipt — investigated, partially mitigated, not fully fixed

Index: [Known gaps](../known-gaps.md)

**Status (re-measured 2026-11-13 at `509cb0d`): PARTIAL.** No `mutation-baseline.json` is committed, so the weekly lane records a baseline instead of gating drift, and no record run has produced one: see "2026-11-13 re-measurement" below for the exact blocker and the bootstrap path. Canary: `tests/test_mutation_workflow_contract.py::test_no_mutation_baseline_is_committed_yet`.

The job's `timeout-minutes` was originally set to 240
with a comment recording that a full baseline run "has taken just over
two hours" at the time (2x headroom). `only_mutate`
(`pyproject.toml`'s `[tool.mutmut]`) has grown since — the module map's
own note under "Test-quality gates (beyond line coverage)" in `AGENTS.md`
records it "now covers identity, suppression and serialization alongside
diff_*/checker_policy" — and the scheduled run on 2026-08-31 (job
`99506019384`) ran the full 240 minutes and was cancelled by that exact
timeout without producing a `mutation-receipt.json`: its own "Run
mutation testing (baseline drift)" step shows starting, then nothing in
the job log until GitHub kills it at the wall-clock ceiling. The three
most recent weekly runs before that (2026-08-17, 08-24, 08-31) all ended
`cancelled` this same way, meaning the per-module baseline-drift gate
had not actually completed a real weekly comparison in that whole
window — a silent gap in exactly the class of coverage `AGENTS.md`'s
own "Mutation testing" section describes as this repo's strongest
test-quality signal, though not a *silent* failure on GitHub's own Actions
tab: the workflow's existing "Flag a cancelled or incomplete run" step
already turns a cancellation into a loud step-summary warning, it just
doesn't make the run finish.
**Mitigated, not closed:** raised `timeout-minutes` to 355 — effectively
GitHub-hosted runners' own hard 360-minute per-job ceiling (not something
a workflow can raise higher), minus a few minutes of buffer for this
job's surrounding checkout/install/save/upload steps. This gives roughly
50% more headroom than the run that was observed failing, but there is no
measurement confirming a full run now completes within it — reproducing
that would mean deliberately running (and waiting out) another multi-hour
scheduled job, which wasn't done here. `mutmut`'s own `max_children`
already defaults to `os.cpu_count()` (parallel mutant execution is not a
missing lever), so if 355 minutes still isn't enough, the real fix is
scoping `only_mutate` down or splitting the weekly run across multiple
scheduled invocations (e.g. half the module list per run, in rotation)
rather than requesting a runner tier this repo has not established it has
access to. Recorded here rather than claimed fixed, per this repository's
own "generalize, or record the gap" convention — this was a direct
timeout-value bump for an observed cancellation, not a verified capacity
fix.

## 2026-11-13 re-measurement: why no baseline is committed

Since this entry was written the lane was split into a 16-shard matrix
(`SHARD_COUNT`, `check_mutation_score.py --shard`), each shard capped at
`MUTMUT_RUN_TIMEOUT_SECONDS = 20_700` inside a 355-minute job. A weekly run
with no committed baseline records one (`--write-baseline`, one
`mutation-baseline.part-<k>.json` per shard), and the `gate` job merges the
parts with `mutation_scope.py merge-baselines`, which refuses any set that
does not partition `only_mutate` exactly. Every weekly run since has failed
(2026-10-12, 10-19, 10-26, 11-02, 11-09).

The 2026-11-09 run (`42650485644`, main at `b5da820`):

| Measure | 2026-11-09 run |
|---|---|
| shards that wrote their part | 10 of 16 (1, 2, 3, 4, 5, 7, 9, 10, 14, 15; job wall time 3h12m to 5h37m) |
| shards stopped by the 20,700 s cap | 6 of 16 (6, 8, 11, 12, 13, 16) — no part written |
| worst shard | shard 12: 194 of 3,673 mutants measured when the cap fired |
| merged baseline | none: `gate` failed at "Every shard must have passed" |

So the blocker is not the job ceiling as such but the shard plan:
`mutation_scope.py` balances shards by **mutant count**, and the cost of a
mutant is the tests that reach it, which differs by orders of magnitude
between functions (shard 12's survivors were in
`compare/finding_redundancy.py`, `diff_filtering.py` and
`diff_types._diff_flexible_array_member`). Raising `SHARD_COUNT` alone is not
evidenced to help: at shard 12's measured rate even 32 shards would not fit.

**Why it was not generated here.** A full record run is ~16 x 3-6 runner-hours
of mutmut against the stats-selected suite. That does not fit a development
container, and a baseline recorded on a different machine would not reproduce
the CI lane's own timeouts and per-test cutoff (`mutmut_stable_param_ids.py`),
so it would not be the reference the lane gates against.

**Bootstrap path.**

1. Start a record run: wait for the Monday schedule, or dispatch `Mutation
   testing` with `write_baseline: true`.
2. For each shard stopped by the cap, use *Re-run failed jobs* on the same run.
   A re-run restores that commit's partial `mutants/` cache
   (`restore-keys: mutmut-<sha>-<shard>-`) and mutmut skips every mutant with a
   stored result, so each attempt continues where the last stopped. Whether the
   per-attempt stats pass leaves room to finish a shard like 12 is not yet
   measured; if it does not, the fix is cost-balanced sharding (weight each
   function by its reaching tests from `tests/mutation_test_selection.txt`'s
   trace) or narrowing `only_mutate`.
3. When every shard has passed, the `gate` job uploads the merged
   `mutation-baseline` artifact. Commit it as `mutation-baseline.json` at the
   repository root.
4. The canary above then fails: archive this entry and turn the canary into a
   check that the committed baseline partitions `only_mutate`.
