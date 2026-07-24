# Reusable Workflows Reference: `check-single.yml` / `check-project.yml`

Two `workflow_call` reusable workflows (G30 P1.4,
[ADR-047](../development/adr/047-github-actions-integration-model.md) §4/§5)
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
    uses: abicheck/abicheck/.github/workflows/check-single.yml@v1
    with:
      name: libfoo
      profile: linux-x86_64-gcc13
      baseline-channel: accepted-main
      baseline-path: ./restored-baseline
      baseline-artifact-name: abicheck-baseline-accepted-main
      requested-depth: headers
      candidate-artifact-name: my-build-output
      new-library: candidate/lib/libfoo.so
```

`candidate-artifact-name`/`baseline-artifact-name`/`build-output-artifact-name`
are all optional (default empty, meaning "no download, use the path as
given") — a caller whose `new-library`/`baseline-path`/`candidate-build-output`
already point at a checked-in fixture doesn't need any of them.

## `check-project.yml`

Three jobs, always in this order:

1. **`plan`** — generates `run-plan.json` (`abicheck run-plan generate`)
   from `inputs.config-path` (default `.abicheck.yml`) plus every downloaded
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
   every report artifact, projects `run-plan.json` to an aggregate manifest
   (`abicheck run-plan to-aggregate-manifest`), and runs
   `abicheck aggregate reports --manifest ...`.

### The two required `if: always()` placements

[ADR-047 §4](../development/adr/047-github-actions-integration-model.md)
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
| `<baseline-artifact-prefix><channel>` | baseline channel referenced by any non-`none` check | that channel's staged baseline-set (`manifest.json` + snapshots, `actions/baseline`'s own output shape). |

All three prefixes are workflow inputs (defaults `abicheck-build-`,
`abicheck-candidate-`, `abicheck-baseline-`) — rename them if they collide
with artifacts your own workflow already produces for another purpose.

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
          name: abicheck-baseline-accepted-main
          path: restored-baseline/

  check:
    needs: [build-linux, fetch-accepted-main-baseline]
    uses: abicheck/abicheck/.github/workflows/check-project.yml@v1
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
