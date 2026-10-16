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

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** .github/workflows/mutation.yml:327 timeout-minutes: 355 (mitigation landed). The entry itself says there is no measurement that a full run completes, and only_mutate scoping/splitting is not done.

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
