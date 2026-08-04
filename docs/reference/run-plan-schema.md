# `run-plan.json` Schema Reference

`run-plan.json` is the ordered list of concrete checks
[ADR-047](../contribute/adr/047-github-actions-integration-model.md) §4/§5
describes: one cell per `(target-or-bundle, profile, checks[] entry)`, each
already carrying its own `check_id`. `abicheck project plan` derives it
from a project's [`.abicheck.yml` `targets:`/`bundles:`/`profiles:`/
`baseline:` block](project-targets-schema.md) (G30 P1.5) plus each `contract:
true` profile's [`build-output.json`](build-output-schema.md) (G30 P1.1).
[`check-project.yml`](reusable-workflows.md)'s matrix and a standalone
`check-single.yml` invocation both consume it.

> **Status.** This page documents the `run-plan.json` schema and the
> `abicheck project plan` command shipped in G30 P1.4 (consolidated from a
> former standalone `run-plan` CLI group by [ADR-054](../contribute/adr/054-cli-project-integration-surface-consolidation.md)).
> See the [reusable workflows reference](reusable-workflows.md) for how
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
doesn't exist on every profile. `project plan` resolves this as follows,
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

A profile with no `--build-output` supplied at all is a hard **error**,
explicit selector or implicit sweep alike — there is nothing to check the
target against, which is different from an implicit sweep's ordinary "this
profile doesn't build the target" skip above (that skip needs an *existing*
`build-output.json` that simply omits the target from its `targets[]` list;
a profile with no build-output artifact at all never got that far).

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
| `compile_gcc_path` | this cell's profile declares `compile.binding` *and* `--toolchain-bindings` was given | That binding, resolved to an exact executable path — forwarded as `check-target`'s `gcc-path` input. Empty (field omitted) when the profile has no `compile:` overlay, declares no `binding`, or `--toolchain-bindings` was omitted/the binding wasn't found in it — a caller then falls back to its own global `gcc-path`. |
| `compile_gcc_options` | this cell's profile's `compile` overlay sets any of `standard`/`stdlib`/`target`/`abi_macros`/`args` | Those axes composed into one space-joined extra-flags string (`-std=<standard> -stdlib=<stdlib> --target=<target> -D<macro>[=<value>] ... <args...>`, macros sorted by name, `args` appended verbatim last) — forwarded as `check-target`'s `gcc-options` input. Not filtered by `compile.compiler_family`: the composed string is always consumed by a Clang-based frontend in this pipeline (castxml's internal bundled Clang, or the direct-clang backend), never a literal GCC binary, so `-stdlib=`/`--target=` are emitted regardless of the declared family — see `_compose_gcc_options`'s own docstring for why an earlier attempt to drop them for `compiler_family: gcc` was reverted. |
| `consumer_compile_gcc_path` | this cell's profile declares `consumer_compile.binding` *and* `--toolchain-bindings` was given | Same resolution as `compile_gcc_path`, but from the profile's separate `consumer_compile:` overlay (G34 Phase 0) — never falls back to `compile_gcc_path`'s own resolved value when the profile has no `consumer_compile:`. |
| `consumer_compile_gcc_options` | this cell's profile's `consumer_compile` overlay sets any of `standard`/`stdlib`/`target`/`abi_macros`/`args` | Same composition as `compile_gcc_options`, from the `consumer_compile:` overlay. |
| `compile_ast_frontend` | this cell's profile's `compile` overlay sets `frontend` | One of `auto`/`castxml`/`clang`/`hybrid` (G34 Phase B), overriding the global `--ast-frontend` default for this profile's cell only. Empty (field omitted) when the profile has no `compile:` overlay or sets no `frontend`. |
| `consumer_compile_ast_frontend` | this cell's profile's `consumer_compile` overlay sets `frontend` | Same resolution as `compile_ast_frontend`, from the profile's separate `consumer_compile:` overlay (G34 Phase 0) — never falls back to `compile_ast_frontend`'s own value when the profile has no `consumer_compile:`. |
| `runs_on` | **always** | The GitHub-hosted runner this cell must be scheduled on, derived from its profile's `os:` (G34 Phase C) — `check-project.yml` reads it as `matrix.runs_on`. `ubuntu-latest` for a profile with no `os:`, which is what every cell hardcoded before this phase. Unlike every other optional field here it is emitted even at its default: a matrix entry missing the key resolves `runs-on:` to the empty string, scheduling nothing. |
| `dependency_source` | this cell's profile declares `dependency_source:` | How this cell provisions its own system dependencies — one of `conda-forge`/`conda-forge-gcc14`/`conda-forge-clang20`/`system`/`none` (G34 Phase C), forwarded as `check-target`'s `dependency-source` input. Empty (field omitted) when the profile declares none, which leaves the caller's workflow-level default standing. |

**`profiles.<id>.compile` reaches the cell (P1 toolchain-profile audit).**
[`project-targets-schema.md`'s `profiles:`](project-targets-schema.md#profiles)
section documents the overlay itself; this generator is the "run-plan
consumer" its `binding` field's docs promised. `compiler_family`/
`compiler_version` are validated shape-wise by `project validate`
but **not** projected into `compile_gcc_path`/`compile_gcc_options` —
`compiler_family` only selects a toolchain through `binding` (there is no
separate "pick a family" flag to forward; the composed `compile_gcc_options`
string is always consumed by a Clang-based frontend in this pipeline, never
a literal GCC binary, so there is nothing correct for `compiler_family` to
gate there), and `compiler_version` is a *constraint* (e.g. `">=14.0,<15"`),
not a value; verifying a resolved binding's actual version against it needs
a real toolchain-identity probe, which stays out of this pure, no-subprocess
module by design.

**`profiles.<id>.consumer_compile` reaches the cell the same way (G34 Phase
0).** [`project-targets-schema.md`'s `consumer_compile:`
section](project-targets-schema.md#consumer_compile-a-separate-client-toolchain-overlay-g34-phase-0)
documents the config-schema side; this generator projects it into its own
separate `consumer_compile_gcc_path`/`consumer_compile_gcc_options` pair,
resolved identically to (but independently of) `compile:`'s own fields.
Actually applying these fields to a distinct header-AST (L2) extraction
pass, merged with the producer toolchain's binary facts, is not yet wired
anywhere in this pipeline — this is config-schema projection only.

**`compile.frontend`/`consumer_compile.frontend` reach the cell the same
way (G34 Phase B).** [`project-targets-schema.md`'s `compile.frontend`
section](project-targets-schema.md#compilefrontend--consumer_compilefrontend--per-profile-ast-frontend-g34-phase-b)
documents the config-schema side; this generator projects each overlay's
`frontend:` into its own field (`compile_ast_frontend`/
`consumer_compile_ast_frontend`), resolved independently.
`check-project.yml`'s check job then forwards `compile_ast_frontend` into
the cell's real invocation as
`${{ matrix.compile_ast_frontend || inputs.ast-frontend }}` — the same
per-cell-first precedence `compile_gcc_path`/`compile_gcc_options` use, so a
GCC profile's cell and a Clang profile's cell in one run genuinely invoke
different frontends. `consumer_compile_ast_frontend` is deliberately *not*
forwarded: it describes the consumer half of the two-pass extraction
`consumer_compile:` above has not built, so there is only one dump
invocation per cell for it to steer, and forwarding it would apply a
consumer overlay to the producer pass.

**A cell schedules itself (G34 Phase C).** `runs_on` and `dependency_source`
are the two axes `check-project.yml` previously fixed for the whole run: every
check cell ran on a hardcoded `ubuntu-latest`, and dependency provisioning came
from one workflow-level `install-deps` boolean. Deriving both per cell is what
makes a genuine GCC/Clang/MSVC matrix schedulable through the shared reusable
workflow — an `os: windows` profile's cell lands on `windows-latest`, and a
GCC-profile cell and a Clang-profile cell in the same run can each provision a
matching conda environment. Unlike the two overlays above, this pair is not
projection-only: `check-project.yml` consumes both today.

Precedence for `dependency_source` matches every other per-profile override
here — the profile's own value wins over the workflow-level
`dependency-source` input, and both empty leaves the legacy `install-deps`
boolean deciding, exactly as before. An `os:` naming no schedulable platform
is a hard error at both `project validate` and `project plan` time rather than
a silent fallback to Linux: a cell scheduled on the wrong platform reports
success having gated the wrong thing.

**No build-output paths are carried through.** `build-output.json` is used
purely as an existence/membership oracle here — the candidate artifact a
real check compares is whatever the *current* run's build produced,
addressed via `binary_pattern`/`consumer_binary_pattern`/
`member_binary_patterns` glob patterns the calling workflow resolves against
a live filesystem (this generator performs no file I/O beyond reading its
own inputs).

## CLI

```bash
abicheck project plan [CONFIG] [--build-output PROFILE=DIR ...] \
    [--project OWNER/REPO] [--head-sha SHA] [--allow-empty] \
    [--format json|text] [-o OUTPUT]
```

`CONFIG` defaults to `.abicheck.yml`. `--build-output` is repeatable — one
per contract profile referenced by `CONFIG`'s `checks:`, where `DIR` is that
profile's `abicheck-build-<profile>/` directory (containing
`build-output.json`). Exit codes:

| Exit | Meaning |
|------|---------|
| `0` | Generated with no coverage-gap errors (warnings may still exist), and at least one check resolved (or `--allow-empty` was given). |
| `1` | A required/explicit check could not be resolved against the supplied `--build-output` directories, or the run-plan resolved to zero checks without `--allow-empty` (ADR-054: fail-closed by default, so a consumer with no guard of its own doesn't silently skip every downstream check). |
| `64` | Usage error — `CONFIG` or a `--build-output` value is unreadable, or `CONFIG` fails `project validate`. |

```bash
abicheck aggregate REPORTS_DIR --run-plan RUN_PLAN_JSON [...]
```

`aggregate --run-plan` (ADR-054) projects `run-plan.json` down to the
expected-target set internally — `abicheck aggregate --manifest`'s
`{"targets": [{"id", "required"}]}` wire shape (ADR-047 §5's required
sub-task) — using each check's own `check_id` as the expected target id,
**never** the bare target/bundle name. `abicheck/aggregate.py`'s target
matching is an exact string comparison against each report's own
`target_id`, and `check-target` (G30 P1.3) always writes that field as the
identical `check_id`-shaped string; projecting to a bare name here would
collide S17/S21's multi-profile/multi-channel same-target checks against
each other in `aggregate`'s duplicate-target-id check. There is no separate
projection command — the former standalone `run-plan to-aggregate-manifest`
step is folded into this option, so a caller never produces (or needs to
know about) an intermediate manifest file.

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

**Full worked example with two toolchain profiles** (`compile_gcc_path`/
`compile_gcc_options` populated, `--toolchain-bindings` in use):
[`tests/fixtures/run_plan/toolchain_matrix/README.md`](https://github.com/abicheck/abicheck/blob/main/tests/fixtures/run_plan/toolchain_matrix/README.md).
