---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - github-actions-surface
---

# Wiring a compatibility check into CI

Offered as the **last** step of a review or release workflow, once a verdict
already exists — never as the workflow itself. "Set up CI" has no
compatibility outcome of its own; it is how a decision the user already made
gets enforced from then on.

Offer it, do not perform it unasked: adding a required check to someone's
pipeline is a project change, and
[safety-invariants.md](safety-invariants.md) item 8 applies.

## What to point at

The GitHub Action and the project-integration lifecycle it implements are
owned by [the GitHub Action page](../../docs/use/github-action.md); the
gating semantics (which exit code fails which job) are owned by
[the CI gating page](../../docs/use/ci-gating.md). Point at those rather than
re-explaining Action inputs — a skill that restates them goes stale the first
time an input is renamed.

## The decisions the user actually has to make

Bring these to them, since they are the parts the docs cannot answer:

1. **Which baseline the gate compares against** — merge base for a PR gate,
   last released version for a release gate. This is the choice that most
   often makes a CI gate useless. See
   [baseline-and-comparability.md](baseline-and-comparability.md).
2. **Which depth is affordable per run.** A `headers`-depth PR gate is cheap
   and catches signature and layout breaks; `build` depth needs the build
   system reachable in CI. See
   [evidence-and-depth.md](evidence-and-depth.md).
3. **What blocks vs. what warns.** The severity/gate configuration is a
   grading decision, not a detector decision. See
   [policies-and-suppressions.md](policies-and-suppressions.md).
4. **Whether the toolchain is pinned.** An unpinned runner image turns every
   image bump into a spurious incident
   ([compiler-and-build-profiles.md](compiler-and-build-profiles.md)).
5. **Whether contract coverage gates.** Under `--contract`,
   incomplete coverage contributes its own orthogonal exit code — decide
   deliberately whether that should fail the job.

## What to hand back

The exact command the gate should run — the same invocation this workflow
just ran, so CI and the local answer stay one semantic model — plus which
exit codes should fail the job, and the baseline-refresh cadence the chosen
baseline implies.
