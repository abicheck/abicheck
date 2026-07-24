# `run-plan.json` Schema Reference

`run-plan.json` is the ordered list of concrete checks
[ADR-047](../development/adr/047-github-actions-integration-model.md) §4/§5
describes: one cell per `(target-or-bundle, profile, checks[] entry)`, each
already carrying its own `check_id`. `abicheck run-plan generate` derives it
from a project's [`.abicheck.yml` `targets:`/`bundles:`/`profiles:`/
`baseline:` block](project-targets-schema.md) (G30 P1.5) plus each `contract:
true` profile's [`build-output.json`](build-output-schema.md) (G30 P1.1).
[`check-project.yml`](reusable-workflows.md)'s matrix and a standalone
`check-single.yml` invocation both consume it.

> **Status.** This page documents the `run-plan.json` schema and the
> `abicheck run-plan` CLI group shipped in G30 P1.4. See the
> [reusable workflows reference](reusable-workflows.md) for how
> `check-project.yml` drives this generator and consumes its output.

## Why a separate artifact

`.abicheck.yml`'s `checks:` entries describe *policy* (which channel, which
depth, required or not) without committing to which profiles actually apply
— an explicit `profiles:` selector, or (more commonly) "every `contract:
true` profile that happens to build this target." Resolving that into a
concrete cell list needs each profile's `build-output.json`, which only
exists after that profile's build has run. Splitting run-plan generation out
as its own artifact means:

- The **plan** step (which needs `build-output.json` from every profile) and
  the **check** step (which fans out over a matrix, potentially across many
  runners) can be separate CI jobs.
- The exact same cell list drives both the matrix (`fromJSON(...)` on
  `checks`) and the trailing `aggregate` gate's expected-target manifest —
  they cannot drift apart, because both read the one file.
- A caller can inspect `run-plan.json` before any check actually runs, to
  confirm coverage looks right.

## Never a blind cross-product

[`project_targets.py`](project-targets-schema.md)'s own docstring flags the
gap ADR-047 §3 warns about: crossing every `checks:` entry with every
`contract: true` profile would produce impossible cells for a target that
doesn't exist on every profile. `run-plan generate` resolves this as follows,
per `checks[]` entry:

- **Explicit `profiles:` selector.** Only those profiles are considered —
  and each one *must* build the referenced target/library (a matching
  `build-output.json` `targets[]` entry), or it's a hard **error**. A caller
  who names a profile explicitly is asserting that cell should exist.
- **No `profiles:` selector (implicit sweep).** Every `contract: true`
  profile is considered, but a profile whose `build-output.json` doesn't
  list the referenced target/library is **silently skipped** — not an
  error, since the whole point of the sweep is "run this on every profile
  where it makes sense."

A profile with no `--build-output` supplied at all is a **warning** for an
implicit sweep (nothing to check it against, but the caller never asked for
that exact profile) and an **error** for an explicit one (the caller
explicitly named a profile the generator can't verify).

## The `app-consumer`/`plugin-contract` library redirect

Neither `target-kind: app-consumer` nor `plugin-contract` ever gets its own
`build-output.json` `targets[]` entry — `build-output.json` describes real
build products, and an app-consumer/plugin-contract target is a *check*, not
a build product (ADR-047 §3). A redirected check's cell existence is gated
on the *referenced library*'s presence on that profile instead, and its
`binary_pattern` is sourced from that library's own `binary_pattern` (never
the contract target's, which doesn't have one) — see `baseline_target` and
`binary_pattern` in the field table below.

## `RunPlanCheck` fields

Field names deliberately mirror
[`actions/check-target/action.yml`](check-target.md)'s own input names
(`kind`, `target_kind` → `target-kind`, `baseline_target` →
`baseline-target`, ...) so a matrix `include:` entry built from one of these
dicts can forward each field through with no renaming.

| Field | Present for | Meaning |
|-------|-------------|---------|
| `check_id` | always | `target@profile#baseline_channel@requested_depth` (ADR-047 §7) — this cell's own reporting identity. |
| `kind` | always | `target` or `bundle`. |
| `name` | always | The target or bundle id. |
| `profile_id` | always | Which profile this cell resolved against. |
| `baseline_channel` | always | The channel this cell's baseline resolves through, or `none`. |
| `requested_depth` | always | `binary` \| `headers` \| `build` \| `source`. |
| `required` | always | Whether a missing report for this cell fails `aggregate`'s coverage gate. |
| `gate_mode` | always | `local` \| `deferred` \| `advisory` (forwarded to `check-target`). |
| `target_kind` | `kind: target` | `library` \| `app-consumer` \| `plugin-contract`. |
| `baseline_target` | `target_kind: app-consumer`/`plugin-contract` | The referenced `kind: library` target's id (empty otherwise — `check-target`'s own `baseline-target` input treats empty as "use `name`"). |
| `binary_pattern` | `kind: target` | Glob pattern (resolved against the *current* build's candidate artifacts by the calling workflow, never by this generator) locating the candidate binary. For a redirected check, the referenced library's own pattern. |
| `consumer_binary_pattern` | `target_kind: app-consumer` | The consumer binary/binaries pattern. |
| `contract_file` | `target_kind: plugin-contract` | The `.syms` contract file path. |
| `bundle_members` | `kind: bundle` | Member target ids. |
| `member_binary_patterns` | `kind: bundle` | Member target id → that member's own `binary_pattern`, so a caller can stage a member-binaries directory without re-reading `.abicheck.yml`. |

**No build-output paths are carried through.** `build-output.json` is used
purely as an existence/membership oracle here — the candidate artifact a
real check compares is whatever the *current* run's build produced,
addressed via `binary_pattern`/`consumer_binary_pattern`/
`member_binary_patterns` glob patterns the calling workflow resolves against
a live filesystem (this generator performs no file I/O beyond reading its
own inputs).

## CLI

```bash
abicheck run-plan generate [CONFIG] [--build-output PROFILE=DIR ...] \
    [--project OWNER/REPO] [--head-sha SHA] [--format json|text] [-o OUTPUT]
```

`CONFIG` defaults to `.abicheck.yml`. `--build-output` is repeatable — one
per contract profile referenced by `CONFIG`'s `checks:`, where `DIR` is that
profile's `abicheck-build-<profile>/` directory (containing
`build-output.json`). Exit codes:

| Exit | Meaning |
|------|---------|
| `0` | Generated with no coverage-gap errors (warnings may still exist). |
| `1` | A required/explicit check could not be resolved against the supplied `--build-output` directories. |
| `64` | Usage error — `CONFIG` or a `--build-output` value is unreadable, or `CONFIG` fails `project-targets validate`. |

```bash
abicheck run-plan to-aggregate-manifest RUN_PLAN_JSON [--head-sha SHA] [-o OUTPUT]
```

Projects `run-plan.json` down to `abicheck aggregate --manifest`'s
`{"targets": [{"id", "required"}]}` wire shape (ADR-047 §5's required
sub-task), using each check's own `check_id` as `targets[].id` — **never**
the bare target/bundle name. `abicheck/aggregate.py`'s manifest matching is
an exact string comparison against each report's own `target_id`, and
`check-target` (G30 P1.3) always writes that field as the identical
`check_id`-shaped string; projecting to a bare name here would collide
S17/S21's multi-profile/multi-channel same-target checks against each other
in `aggregate`'s duplicate-target-id check.

## Example

```json
{
  "schema": "abicheck.run-plan/v1",
  "project": "acme/foo",
  "head_sha": "deadbeef",
  "checks": [
    {
      "check_id": "libfoo@linux#release@headers",
      "kind": "target",
      "name": "libfoo",
      "profile_id": "linux",
      "baseline_channel": "release",
      "requested_depth": "headers",
      "required": true,
      "gate_mode": "local",
      "target_kind": "library",
      "binary_pattern": "build/libfoo*.so"
    }
  ]
}
```
