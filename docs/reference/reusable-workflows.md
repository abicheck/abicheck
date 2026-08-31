# Reusable Workflows Reference: `check-single.yml` / `check-project.yml`

Two `workflow_call` reusable workflows (G30 P1.4,
[ADR-047](../contribute/adr/047-github-actions-integration-model.md) §4/§5)
built on top of [`actions/check-target`](check-target.md):

- **`check-single.yml`** — a thin wrapper around one `check-target`
  invocation, for a caller that wants exactly one check without generating a
  [`run-plan.json`](run-plan-schema.md).
- **`check-project.yml`** — the full multi-target flow: generate
  `run-plan.json`, fan it out over a matrix (one `check-target` invocation
  per cell), then a trailing `aggregate` job that projects `run-plan.json`
  to `abicheck aggregate --manifest`'s wire shape and computes the fan-in
  gate decision.

> **Status.** Shipped in G30 P1.4. The artifact-staging convention
> `check-project.yml` expects from its caller (below) is new with this
> workflow and has not yet been exercised against a real external-consumer
> run — no second repository was available in the session that built this
> to validate cross-repo artifact staging end to end. Treat it as reviewed-
> but-unverified-in-a-live-CI-run, the same honesty this plan's own status
> notes use elsewhere for parts that could only be validated against a
> hand-authored fixture.

## `check-single.yml`

Every input mirrors [`actions/check-target/action.yml`](check-target.md)'s
own input surface 1:1 (same names, same defaults) — see that page for the
full rationale behind each one. Outputs are `check-target`'s own six
outputs, forwarded unchanged, plus `report-artifact-name`.

`report-path` is a path inside this job's own ephemeral runner workspace —
not reachable by the calling workflow directly (a `workflow_call` job runs
on a separate runner, same caveat as the artifact-staging inputs below).
The job unconditionally uploads the report (`if: always() &&
steps.run.outputs.report-path != ''`, same condition `check-project.yml`
uses for each matrix cell) under `<inputs.report-artifact-prefix><sanitized
check-id>` (prefix default `abicheck-check-single-report-`) — the same
prefix-plus-sanitized-check-id convention `check-project.yml` uses for each
matrix cell's own report artifact, so a caller invoking `check-single.yml`
more than once in one workflow run (a matrix, or several single-check jobs)
doesn't collide on `actions/upload-artifact`'s per-run name-uniqueness
requirement. The full computed name is echoed back as the
`report-artifact-name` output so a caller can `download-artifact` it
without re-deriving the sanitization.

**This job always runs in its own fresh, isolated runner** — unlike
`check-target` itself (a composite Action a caller can nest as one step
inside their own existing job, sharing that job's filesystem), a
`workflow_call` reusable workflow's job never shares a filesystem with the
caller's own build job. A `new-library`/`baseline-path`/`candidate-build-output`
path only exists here if it's checked into git (present after this job's
own checkout) or explicitly staged as a `download-artifact` step — which is
exactly what the three optional `*-artifact-name` inputs below do, mirroring
`check-project.yml`'s own artifact-staging convention:

```yaml
jobs:
  check-libfoo:
    uses: abicheck/abicheck/.github/workflows/check-single.yml@c9e135a3233b6d45e9571533f71293fde458a469  # not yet in a tagged release; pin main or newer
    with:
      name: libfoo
      profile: linux-x86_64-gcc13
      baseline-channel: accepted-main
      baseline-artifact-name: abicheck-baseline-accepted-main
      requested-depth: headers
      candidate-artifact-name: my-build-output
      new-library: candidate/lib/libfoo.so
```

`candidate-artifact-name`/`baseline-artifact-name`/`build-output-artifact-name`
are all optional (default empty, meaning "no download, use the path as
given") — a caller whose `new-library`/`baseline-path`/`candidate-build-output`
already point at a checked-in fixture doesn't need any of them.
When `baseline-artifact-name` is set, the workflow downloads it only into its
private `.check-single-baseline` staging directory and passes that fixed path
to `check-target`; `baseline-path` is ignored in that mode. This confinement
prevents a caller-controlled path from deleting or overwriting the workflow's
self-checkout (including the local Action executed afterward).

## `check-project.yml`

Three jobs, always in this order:

1. **`plan`** — generates `run-plan.json` (`abicheck project plan
   --allow-empty`, deferring the empty-plan guard to the `no-checks` job
   below rather than the command's own fail-closed default) from
   `inputs.config-path` (default `.abicheck.yml`) plus every downloaded
   `<build-output-artifact-prefix><profile-id>` artifact, uploads it under
   `inputs.run-plan-artifact-name`, and exposes its `checks[]` as a matrix
   `include:` list (job output `matrix`) plus a `has-checks` flag.
2. **`check`** (matrix, `needs: plan`, `fail-fast: false`) — one
   `check-target` invocation per `run-plan.json` cell. Downloads that cell's
   candidate artifact and (unless `baseline_channel: none`) that channel's
   baseline-set artifact, resolves the candidate binary/binaries from
   `binary_pattern`/`member_binary_patterns` via a glob against the
   downloaded candidate tree, runs `check-target`, and — **unconditionally**
   (`if: always()`) — uploads the resulting report under
   `<report-artifact-prefix><check_id>`.
3. **`aggregate`** (`needs: [plan, check]`, **`if: always()`**) — downloads
   every report artifact and runs `abicheck aggregate reports --run-plan
   run-plan.json ...`, which projects `run-plan.json` to the expected-target
   set internally (no separate projection step or intermediate manifest
   file, ADR-054).

### The two required `if: always()` placements

[ADR-047 §4](../contribute/adr/047-github-actions-integration-model.md)
flags two specific places this workflow must use `always()` (or
`!cancelled()`), not a bare `needs:`/no condition — both because plain
GitHub Actions semantics **skip** a dependent job or step when an earlier
one in its chain fails, and a **skipped** job/step reports `success`:

- The **`aggregate` job** itself. Without `if: always()`, one matrix leg
  operationally failing under `gate-mode: deferred` (exactly the case where
  that leg is *expected* to fail its own job — that visibility is the
  point) would skip `aggregate` entirely, and a skipped job reporting
  success would silently green a branch-protection-required status past a
  missing target — the exact failure mode ADR-047 exists to close.
- The matrix job's **`Upload report` step**. `check-target`'s own exit
  (after its finalize step already wrote the report) can still fail the
  *step* calling it, and a step in a job whose earlier step failed is
  skipped by default unless it too carries `always()`. Without this, the
  report for exactly the failing cells `aggregate` most needs to see would
  never upload.

The `Run check-target` step deliberately carries **no** `continue-on-error`:
letting its natural failure propagate is what makes the matrix job's own
conclusion correctly reflect a real `gate-mode: local` break or an
operational error — `steps.run.outputs.*` stay populated even for a failed
step (they were written by `check-target`'s internal finalize step before
its own exit code was returned), so the always()-conditioned `Upload report`
step still sees them.

### Pre-check failures (candidate resolution, build-output download)

Before `check-target` ever runs, the matrix job resolves this cell's
candidate binary/binaries (`binary_pattern`/`member_binary_patterns`, a
glob against the downloaded `candidate/` artifact) and, when a baseline or a
wrapper/clang-plugin evidence pack is needed, downloads that cell's
`build-output.json`. Either can genuinely fail — no candidate matched, an
ambiguous/escaping pattern, a missing bundle member, or a required
build-output download error. A **"Synthesize pre-check operational-error
report"** step catches exactly this: when either fails, it writes a full
operational-error report envelope (`verdict: "ERROR"`,
`operational_errors: [{"kind": "ambiguous", ...}]`) by calling
`actions/check-target/report_envelope.py --mode operational-error` directly
— the same script `check-target`'s own finalize step drives for a real
`resolve-baseline` failure — so `aggregate` sees a typed, per-cell failure
here too, rather than a cell that silently vanished from the report set (as
if it had never been required at all). `Run check-target` itself is gated
to skip whenever candidate resolution didn't succeed, and the downstream
`Sanitize check-id for artifact name`/`Upload report` steps pick up whichever
of the two report-producing steps actually ran.

### Required artifact-staging convention

`check-project.yml` never builds anything and never fetches from a baseline
channel's storage backend itself — the same "this Action never fetches"
boundary [`actions/resolve-baseline`](resolve-baseline.md) and
`actions/baseline` already draw (ADR-047 §10). The calling workflow's own
job(s) must upload, before this reusable workflow's jobs need them:

| Artifact name | One per | Contents |
|---|---|---|
| `<build-output-artifact-prefix><profile-id>` | contract profile | that profile's `abicheck-build-<profile>/` directory ([build-output.json](build-output-schema.md) + whatever it references) — G30 P1.1. |
| `<candidate-artifact-prefix><profile-id>` | contract profile | the tree each target's `binary_pattern`/`consumer_binary_pattern` globs against for this run's candidate side. |
| `<baseline-artifact-prefix><profile-id>-<channel>` | (contract profile, baseline channel) pair with any non-`none` check on that profile | that pair's staged baseline-set (`manifest.json` + snapshots, `actions/baseline`'s own output shape). Keyed by profile as well as channel — a baseline-set is itself profile-specific (`actions/baseline`'s manifest records exactly one `profile`; `resolve-baseline` rejects a mismatch as `wrong_profile`), so two profiles sharing one channel each need their own artifact. |

All three prefixes are workflow inputs (defaults `abicheck-build-`,
`abicheck-candidate-`, `abicheck-baseline-`) — rename them if they collide
with artifacts your own workflow already produces for another purpose.

**Every uploaded `build-output.json` must set `profile.id`.** The `plan`
job identifies which downloaded `build-output.json` belongs to which
profile by reading each file's own `profile.id` field, not the artifact or
directory name (`actions/download-artifact` flattens a single-artifact
match with no subdirectory, so the name is ambiguous by construction) — see
[the schema reference](build-output-schema.md#schema-abicheckbuild-outputv1)
for the full requirement. A `build-output.json` with no `profile.id` set
fails the `plan` job outright, even though that field is optional in the
schema generally.

For a target carrying `targets[].evidence`, the check cell resolves both the
pack path and its `evidence_producer.kind` from this same downloaded manifest.
It never substitutes the workflow-global pack path for a target with no
declared evidence; the global producer input remains only for replay and
legacy callers without a target evidence entry.

```yaml
jobs:
  build-linux:
    runs-on: ubuntu-latest
    steps:
      # ... your existing build, producing abicheck-build-linux/build-output.json ...
      - uses: actions/upload-artifact@v7
        with:
          name: abicheck-build-linux
          path: abicheck-build-linux/
      - uses: actions/upload-artifact@v7
        with:
          name: abicheck-candidate-linux
          path: build/lib/

  fetch-accepted-main-baseline:
    runs-on: ubuntu-latest
    steps:
      # ... restore from actions/cache, a release asset, or git, per ADR-047 §10 ...
      - uses: actions/upload-artifact@v7
        with:
          name: abicheck-baseline-linux-accepted-main
          path: restored-baseline/

  check:
    needs: [build-linux, fetch-accepted-main-baseline]
    uses: abicheck/abicheck/.github/workflows/check-project.yml@c9e135a3233b6d45e9571533f71293fde458a469  # not yet in a tagged release; pin main or newer
    with:
      config-path: .abicheck.yml
```

### Shared analysis options

`check-project.yml` accepts one project-wide value for every analysis option
`check-target` supports (`header`/`policy`/`severity-preset`/`gcc-*`/...),
forwarded unchanged to every matrix cell. **A per-cell override of any of
these is out of scope for this first version** — if different targets need
different policy/suppression files, run them through separate
`check-project.yml` calls (one per differing option set) until a later
iteration extends `run-plan.json`'s schema to carry per-cell overrides.

**Exception: `gcc-path`/`gcc-options` do get a per-cell override**, from
that cell's `.abicheck.yml` `profiles.<id>.compile` overlay (P1
toolchain-profile audit — [`project-targets-schema.md`](project-targets-schema.md#profiles)),
when `toolchain-bindings-path` is set. `abicheck project plan
--toolchain-bindings <path>` (run by the `plan` job) resolves each
profile's declared `compile.binding` logical id (e.g. `"gcc14"`) against
that trusted, separately-managed mapping file into an exact executable
path, and composes `compile.standard`/`stdlib`/`target`/`abi_macros`/`args`
into one extra-flags string — both land on the generated cell as
`compile_gcc_path`/`compile_gcc_options`
([`run-plan-schema.md`](run-plan-schema.md#runplancheck-fields)) and
**replace** this workflow's own global `gcc-path`/`gcc-options` inputs for
that cell only — not merge with them. If the project relies on a flag set
via the workflow's global `gcc-options` input (e.g. a universal `-fPIC`)
*and* wants a per-profile `compile:` overlay too, that global flag will not
carry over to overlaid cells; repeat it inside the overlay's `args` if it
still needs to apply there. A profile with no `compile:` overlay (or a run
with `toolchain-bindings-path` left empty, the default) falls back to the
global inputs unchanged — no behavior change for a project that doesn't use
this. `compiler_family`/`compiler_version` are validated shape-wise but not
yet projected into any forwarded flag (see `run-plan-schema.md`'s field
table for why).

**Exception: `ast-frontend` also gets a per-cell override**, on the same
precedence rule (G34 Phase B). A profile's `compile.frontend:`
([`project-targets-schema.md`](project-targets-schema.md#compilefrontend-consumer_compilefrontend-per-profile-ast-frontend-g34-phase-b))
reaches its cells as `compile_ast_frontend` and replaces this workflow's own
`ast-frontend` input there — so a GCC profile's cell can parse headers with
castxml while a Clang/DPC++ profile's cell in the same run uses
`clang -ast-dump=json`, which one workflow-global value cannot express. A
profile that sets no `frontend:` falls back to the global input unchanged.

**`kind: bundle` cells are excluded from this override.** A bundle cell's
operand is the `bundle-staging` *directory* it stages its members into, and
the root Action rejects every non-`auto` `ast-frontend` for a
directory/package operand outright — the per-library fan-out never threads an
L2 compile context to each pair's header dump, so the requested frontend
could not be applied and silently dropping it would parse headers under the
wrong one. A bundle cell therefore keeps resolving the workflow-global
`ast-frontend` input exactly as it did before this override existed.

The sibling `consumer_compile.frontend`, compiler binding, and options are
forwarded to a separate candidate dump. That invocation reads the same
producer binary under the client header context, and the comparison consumes
the materialized snapshot without reparsing candidate headers. An omitted
field falls back to this same workflow's global input (`ast-frontend`/
`gcc-path`/`gcc-options`), never to the empty string — an overlay that sets
only `standard:`, say, still uses the caller's selected frontend for the
consumer dump rather than silently reverting to the CLI default.

**Known gap: only this candidate-side dump exists.** The baseline (old)
side of a real `baseline-channel` comparison is produced by
`publish-baseline.yml`/`update-main-baseline.yml` long before this job
runs, and neither reads a profile's `consumer_compile:` overlay — see
`project-targets-schema.md`'s own "Known gap" note above for what this
means for a `consumer_compile:` check compared against a real baseline
(it currently resolves `NOT_COMPARABLE` rather than a wrong verdict).

Every *other* analysis option above stays global-only, unaffected by these
three exceptions.

**Exception: `dependency-source` also gets a per-cell override**, on the same
precedence rule (G34 Phase C). A profile's own `dependency_source:`
([`project-targets-schema.md`](project-targets-schema.md#os-and-dependency_source-how-a-profile-schedules-its-own-check-cell-g34-phase-c))
wins over this workflow's `dependency-source` input for that profile's cells
only, so a GCC-profile cell and a Clang-profile cell in one run can each
provision a matching conda environment instead of sharing whatever the
workflow-level value said. With both unset the legacy `install-deps` boolean
still decides, exactly as before.

**Exception: `header` also gets a per-cell override**, on the same
precedence rule. A `kind: library` target's own `public_headers:`
([`project-targets-schema.md`](project-targets-schema.md#targets)) —
space-joined into `run_plan.RunPlanCheck.header` — wins over this workflow's
`header` input for that target's cells only, so a project whose libraries
each have their own header tree gets that scoping automatically once
declared, instead of every cell sharing one workflow-global `header` value.
An `app-consumer`/`plugin-contract` target has no `public_headers:` of its
own and redirects through its `library:` target's, the same way its
`binary_pattern` already does. A target that declares none falls back to
the workflow-global `header` input unchanged, so a project that only ever
set the global value sees no behavior change. **`kind: bundle` cells are
excluded from this override**, for the same reason `ast-frontend` is: a
bundle cell's candidate is the staged directory of every member's own
binary, and there is no per-bundle-member header staging mechanism to give
each member's binary its own header tree in that one directory comparison
— see `BUNDLE_CHECK_DEPTHS`'s own docstring in `project_targets.py`. A
bundle cell keeps resolving the workflow-global `header` input exactly as
it did before this override existed.

**Each cell is scheduled on its profile's own runner** (G34 Phase C), rather
than the `ubuntu-latest` every cell used to hardcode: the `plan` job derives
`runs_on` from each profile's `os:`, so an `os: windows` profile's cell lands
on `windows-latest`. A profile with no `os:` — every profile written before
this phase — still resolves to `ubuntu-latest`, so no existing project's
scheduling moves. Note this makes `os:` load-bearing: a value naming no
schedulable platform now fails `project validate` instead of being ignored.
An actual native `windows-latest` lane running a real MSVC profile end to end
through this workflow is separate, still-open work — this phase lands the
scheduling mechanism, not a validated MSVC fixture project.

**Give each parallel call its own artifact names.** `actions/upload-artifact`
requires unique names within one workflow *run* — two `check-project.yml`
calls in the same run that both leave `run-plan-artifact-name` /
`aggregate-artifact-name` at their shared defaults will fail at the upload
step before either finishes any check (Codex review); leaving
`report-artifact-prefix` shared is worse and silently wrong rather than
failing loud — the `aggregate` job downloads by
`pattern: '<report-artifact-prefix>*'`, so it would pull in the *other*
call's per-cell reports too and either misreport coverage or hit
`aggregate`'s duplicate-target-id rejection. Set distinct values for
`run-plan-artifact-name`, `aggregate-artifact-name`, and
`report-artifact-prefix` on every parallel call (a per-call suffix, e.g. the
target/option-set name, is enough); do the same for
`build-output-artifact-prefix`/`candidate-artifact-prefix`/
`baseline-artifact-prefix` too unless the calls intentionally share the same
profile/channel artifacts (harmless when they do — same content, downloaded
twice).

### Outputs

| Output | Meaning |
|---|---|
| `gate-exit-code` | `abicheck aggregate`'s own exit code (`0` pass / `1` coverage-or-policy gap / `2` API break / `4` ABI break). |
| `run-plan-artifact-name` | Echoes `inputs.run-plan-artifact-name`, for a caller that wants to download it too. |

## Self-checkout: how the nested Actions actually resolve

Both workflows' steps reference `check-target` (and, transitively,
`resolve-baseline`/`collect-facts`/the root Action) via a relative
`uses: ./x` path. A relative path inside a *reusable workflow's own steps*
resolves against the **caller's** checkout, never against the repository
that defines the reusable workflow — the identical limitation
[`check-target`'s own composite-Action nesting](check-target.md) already had
to work around, confirmed for reusable workflows specifically via GitHub
Community Discussion #107558 ("How can callable workflows in a dedicated
repo use its local actions with relative paths?").

The fix mirrors `check-target`'s own: check out this exact repository/ref
into a side directory first, then reference every nested `uses:` relative to
that directory. The reusable-workflow equivalent of `check-target`'s
`github.action_repository`/`github.action_ref` (which describe the
composite *Action* about to run) is `job.workflow_ref`/`job.workflow_sha`
(part of the `job` context, populated specifically so a reusable workflow
can identify itself independent of the calling workflow's own `github.*`
context) — always the fully-qualified `owner/repo/.github/workflows/
check-single.yml@ref` form. **Not** `github.workflow_ref`/
`github.workflow_sha`: GitHub's docs are explicit that "when a reusable
workflow is triggered by a caller workflow, the `github` context is always
associated with the caller workflow," so those fields resolve to the
*calling* repository/ref for any external consumer — the opposite of what
a self-checkout needs. Both workflows fall back to `github.repository`/
`github.sha` if `workflow_ref` is ever empty, matching `check-target`'s own
defense-in-depth pattern for the equivalent local-same-repository case.
