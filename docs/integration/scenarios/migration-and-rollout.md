# Scenarios S26 & S27: Migration and Intentional Breaks

Two situations where the check itself doesn't change, but how it's allowed
to affect CI does — [ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8 names them S26 and S27:

- **S26 — shadow rollout / migrating from another ABI tool.** You're
  adopting abicheck alongside an existing tool (or introducing ABI checks to
  a project that never had them), and want to see what it *would* report
  before it can block anyone's PR.
- **S27 — an intentional breaking change.** A specific PR genuinely needs to
  ship an ABI break (a deliberate major-version bump, a symbol nobody should
  have depended on), and the gate needs a scoped, visible relaxation — not a
  blanket "disable the check."

## S26: `gate-mode: advisory`

Run the check for real, report for real, but never fail the job on a
compatibility finding — only a genuine operational error still fails
(`gate-mode`'s advisory relaxation only ever covers the *compatibility*
verdict, never an infrastructure problem):

```yaml
- uses: abicheck/abicheck/actions/check-target@c9e135a3233b6d45e9571533f71293fde458a469  # not yet in a tagged release; pin main or newer
  with:
    name: libfoo
    gate-mode: advisory
    # ... rest of the check as in any other scenario ...
```

Keep your old tool (if any) running in parallel and required, exactly as
before — nothing forces its removal. Once you're confident in abicheck's
findings for this target (no unexpected findings for a stretch of real PRs),
flip `gate-mode` to `local`/`deferred` and retire the old tool's own gate.
See [CI Gating: How the Pieces Fit Together](../../use/ci-gating.md)
for the full exit-code/gate-mode interaction model.

## S27: a scoped, visible relaxation for one PR

The report must stay visible — an intentional break is still a real,
recorded finding, not a suppressed one. Two independent levers, pick
whichever fits:

- **Per-PR**: relax the *gate* only for this PR (e.g. a label-triggered
  `gate-mode: advisory` override, or a `severity-addition`/suppression entry
  scoped to the specific finding and reviewed in the PR diff) — the
  `release-contract` channel is untouched by this: nothing about relaxing
  one PR's gate changes what the *next* release's baseline records as
  accepted.
- **Post-merge**: once merged, the next `update-main-baseline.yml` run
  naturally picks up the new surface as `accepted-main`'s new baseline — no
  manual step needed for that channel specifically.

See [CI Gating](../../use/ci-gating.md)'s recipes section for
suppression- and severity-based scoping mechanics.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [CI Gating: How the Pieces Fit Together](../../use/ci-gating.md) — the full exit-code/gate-mode/suppression model.
- [`publish-baseline`/`update-main-baseline` Reference](../../reference/publish-baseline.md) — how `accepted-main` picks up a merged change automatically.
