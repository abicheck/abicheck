# Scenario S1: One Library, Baseline Committed in the Repo

You maintain one shared library. Its previous accepted ABI/API surface is a
snapshot file (`baseline.json`, `abicheck dump`'s output) checked directly
into the repository — no release process, no separate baseline-storage
backend, nothing to fetch. This is the minimal onboarding case
[ADR-047](../../development/adr/047-github-actions-integration-model.md) §8
calls S1: "the root Action alone suffices, no new primitive needed."

## What you need

1. A candidate binary (built by an earlier step in the same job, or checked
   out from a release artifact).
2. A committed baseline snapshot.
3. Nothing else — no `.abicheck.yml` `targets:`/`profiles:`/`baseline:`
   block, no reusable workflow, no separate baseline-publishing job. Those
   exist for the scenarios *after* this one (multiple targets, multiple
   profiles, a baseline that refreshes itself) — adding them here before you
   need them is pure overhead.

## The check

Call the root Action directly in your PR workflow, pointing `old-library` at
the committed snapshot and `new-library` at the just-built candidate:

```yaml
- uses: abicheck/abicheck@v1
  with:
    old-library: baseline.json
    new-library: build/lib/libfoo.so
    header: include/
```

That's the whole integration. See the
[GitHub Action reference](../../user-guide/github-action.md) for every input/
output this Action accepts, and
[Creating and Comparing a Baseline](../../user-guide/create-baseline.md) for
how `baseline.json` itself gets produced and refreshed.

## When to move past this scenario

- **More than one library** → [S15](../index.md) (multiple independent
  targets) or [S14](../index.md) (one release bundle) — see
  [`check-project.yml`](../../reference/reusable-workflows.md)'s matrix, not
  N copies of this same step.
- **The baseline should track the last release or `main`, not a file you
  update by hand** → [S2](../index.md)/[S19](../index.md)/[S20](../index.md)
  — [`publish-baseline`/`update-main-baseline`](../../reference/publish-baseline.md).
  `check-single.yml`'s `baseline-channel`/`baseline-artifact-name` inputs are
  the bridge once you have one of those.
- **Your build is expensive and this step re-parses a binary a separate job
  already built** → [S3: Reuse an Existing Build](existing-build-artifact.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [GitHub Action reference](../../user-guide/github-action.md) — every input/output.
- [Choose Your Workflow](../../user-guide/choose-your-workflow.md) — the
  CLI-command-level table this scenario's Action call is built on.
