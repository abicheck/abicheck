# Scenario S25: Monorepo / Multiple Components

Your repository holds several independently-versioned components (not a
release bundle — see [S14](release-bundle.md) if they ship together with
cross-dependencies), and a PR usually only touches one or two of them. You
don't want every PR running every component's full check matrix.
[ADR-047](../../development/adr/047-github-actions-integration-model.md)
§8's S25: the [run plan](../../reference/run-plan-schema.md) may be filtered
by what changed, but **required-target coverage stays fail-closed** —
filtering must never silently drop a required check with no error.

## The foundation is [S15](multi-dso-project.md)

Structurally, a monorepo's components are exactly S15's "several independent
targets, one build" — declare each component as its own `.abicheck.yml`
target, each with its own `checks:`. What's specific to S25 is *scoping*
which of those targets a given run actually checks.

```yaml
targets:
  component-a:
    binary_pattern: "components/a/lib/*.so"
    checks:
      - channel: accepted-main
        depth: headers
  component-b:
    binary_pattern: "components/b/lib/*.so"
    checks:
      - channel: accepted-main
        depth: headers
```

> **No built-in changed-path filter yet.** `abicheck run-plan generate`
> ([Run Plan Schema](../../reference/run-plan-schema.md)) does not currently
> accept a `--changed-path`/`--since` selector to filter `checks[]` down to
> only the components a given diff touched — every declared target's checks
> are always in the generated plan. Until that lands, scope a monorepo PR
> workflow yourself: compute the changed paths (e.g. `git diff --name-only`
> against the PR's base) in your own CI step, and conditionally skip the
> `check-project.yml` call (or a specific matrix cell) **only for
> non-required components** the diff didn't touch. Any target/check marked
> `required: true` must stay fail-closed — either run it unconditionally
> regardless of what changed, or, if you do skip it, have your own gating
> step independently verify its coverage (e.g. assert the skipped check's
> `check_id` still appears with a pass/not-applicable result before the
> workflow can succeed). Silently omitting a required check because your own
> path filter decided it was "unaffected" is exactly the coverage gap this
> scenario's opening paragraph warns against — the filter you write is not
> covered by abicheck's own fail-closed guarantees, only your own targets
> that already went through `check-project.yml` are.

## When to move past this scenario

- **Your components actually depend on each other and should be checked as
  one bundle** → [S14: Multi-DSO Release Bundle](release-bundle.md).
- **Different components need different build profiles** → combine with
  [S17: Multiple Build Profiles](multi-platform.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [S15: Multiple Independent Targets](multi-dso-project.md) — the structural foundation this scenario scopes.
- [Run Plan Schema](../../reference/run-plan-schema.md) — the full `run-plan.json` contract.
