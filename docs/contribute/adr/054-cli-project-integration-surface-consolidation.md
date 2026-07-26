# ADR-054: CLI Project-Integration Surface Consolidation

**Date:** 2026-07-26
**Status:** Accepted — implemented.
**Decision maker:** (pending)

---

## Context

ADR-043 reset the public root command surface to a small, stable set of
user-facing verbs and pinned the invariant as an executable test
(`tests/test_cli_root_surface.py`): "nothing else is registered on the root
`Click` group." D13 grew that set from five to six with `aggregate`, a
report-level fan-in gate that clears ADR-037 D7's "different question,
different operand shape" bar.

ADR-047's G30 project-integration work then grew the root surface again —
but differently. Where D13 added *one* verb for *one* new question, G30
added a **new root command for each new intermediate pipeline artifact** as
it was built, over several PRs:

- `build-output validate`/`build-output baseline-libraries` (G30 P1.1/P1.6) —
  validates, and derives an Action input from, `build-output.json`.
- `project-targets validate` (G30 P1.5) — validates `.abicheck.yml`'s
  `targets:`/`bundles:`/`profiles:`/`baseline:` block.
- `run-plan generate`/`run-plan to-aggregate-manifest` (G30 P1.4) — derives
  `run-plan.json` from the two artifacts above, and projects it to
  `aggregate --manifest`'s wire shape.
- `plan --dump-manifest` (ADR-050 D3, G32 Phase B) — parses and fingerprints
  a `--dump-manifest` document without extracting.

Each addition was individually defensible at the time it landed — every one
of them does something real and none is redundant with an existing command.
But by the time all four existed, the root surface was ten commands, and an
external review of a fresh `main` found the same failure mode ADR-043 D1
exists to prevent, recurring one artifact at a time instead of all at once:

- The root surface **mirrored an internal pipeline** (project config → build
  output → run plan → per-target checks → aggregate) rather than user-facing
  operations. A user's actual question — "is my multi-target project's ABI
  contract intact?" — was answered by chaining five separate root commands
  (`project-targets validate` → `build-output validate` → `run-plan
  generate` → `run-plan to-aggregate-manifest` → `aggregate`), most of which
  exist only because the previous one produces a file the next one reads.
- Two of the ten commands (`build-output baseline-libraries`, `run-plan
  to-aggregate-manifest`) are not general project-integration operations at
  all — the first is a wire-format adapter for exactly one GitHub Action's
  `libraries` input, and the second is a pure intermediate-format
  conversion consumed by exactly one downstream command (`aggregate
  --manifest`). Neither answers a question a user asks; both exist purely to
  make one artifact consumable by one specific consumer.
- `plan --dump-manifest` duplicated the "resolve, don't execute" vocabulary
  ADR-043 D9 already established as `--dry-run` — a manifest preflight is
  exactly what `dump --dry-run` already promises to do for every other
  input shape, so a second, parallel, differently-named preflight command
  was an avoidable second vocabulary for the same concept.
- `README.md` and ADR-043 itself still said the CLI is six commands, "nothing
  else registered" — while the actual `main.commands` set had drifted to
  ten. The product story, the ADR, and the implemented interface had
  diverged, exactly the drift D1's executable pin was supposed to make
  impossible — except the pin was updated (D13-style) for `aggregate` but
  never extended to cover the four G30/ADR-050 additions, so it silently
  stopped being the actual contract.

The underlying project-integration model (ADR-047: project config, build
profile, target, build output, baseline channel, check, run plan, report,
fan-in) is not in question here — it is sound, and real external pilots
(PVXS, CMake/Make/Bazel builds, `.deb` packaging, aarch64 cross-builds, Intel
oneAPI) exercised it successfully. What needs correcting is only how much of
its *internal* structure was promoted to *public root CLI surface*.

---

## Decision

### D1. Root surface grows to seven, consolidated: `project` replaces four groups

The public root surface (amending ADR-043 D1/D13) is now `dump`, `compare`,
`scan`, `deps`, `compat`, `aggregate`, `project`. `build-output`,
`project-targets`, `run-plan`, and the standalone `plan` command are removed
outright — no deprecated shim, matching ADR-043's own "pre-1.0, no alias
mechanism" policy. Three of their four subcommands move under one new
`project` group (`abicheck/cli_project.py`):

| Was | Now |
|---|---|
| `build-output validate` | `project validate-build` |
| `project-targets validate` | `project validate` |
| `run-plan generate` | `project plan` |

`project` is deliberately **not** a peer of the five core-analysis verbs in
the root `--help` panel grouping (`cli_help.py`'s `COMMAND_GROUPS`) — it gets
its own "Project integration (advanced)" panel, distinct from "Core
analysis" and "Workflow composition." Most libraries never touch it; it
exists for projects checking several targets/build profiles/baseline
channels together through the reusable `check-project.yml` workflow.

### D2. Two subcommands are dropped from the public CLI entirely

`build-output baseline-libraries` and `run-plan to-aggregate-manifest` do
not move to `project` — they are removed as CLI surface, full stop:

- **`build-output baseline-libraries`** derives `actions/baseline`'s
  `libraries` JSON input from `build-output.json`. Its only two callers
  (`publish-baseline.yml`, `update-main-baseline.yml`) now call
  `abicheck.buildsource.baseline_publish.derive_baseline_libraries()`
  directly via an inline `python3 -c` step — the function was always the
  real implementation; the CLI wrapper added a process boundary and a
  public-surface commitment for a caller that was never anything but these
  two reusable workflows.
- **`run-plan to-aggregate-manifest`** projected `run-plan.json` to
  `aggregate --manifest`'s `{"targets": [...]}` shape. `aggregate` gains a
  new `--run-plan RUN_PLAN_JSON` option instead (D3) that performs the same
  projection internally — a caller no longer needs to know the projection
  exists as a separate step, let alone invoke it.

Both functions (`derive_baseline_libraries`, `to_aggregate_manifest`) are
unchanged and remain covered by their own unit tests
(`tests/test_baseline_publish.py`, `tests/test_run_plan.py`); only the CLI
entry points are gone.

### D3. `aggregate --run-plan` folds the projection into the gate itself

`abicheck aggregate REPORTS_DIR --run-plan run-plan.json` is a fourth,
mutually-exclusive alternative to `--manifest`/`--expect`+`--optional`/
`--discovered-only` for supplying the expected-target set: it loads
`run-plan.json`, projects it to the manifest shape with the existing
`to_aggregate_manifest()`, and feeds that straight to
`ExpectedTargets.from_manifest_data()`. `check-project.yml`'s `aggregate`
job now runs one step (`abicheck aggregate reports --run-plan run-plan.json
...`) where it previously ran two (`run-plan to-aggregate-manifest` writing
an intermediate `aggregate-manifest.json`, then `aggregate --manifest
aggregate-manifest.json`).

### D4. `plan --dump-manifest` folds into `dump --dump-manifest --dry-run`

`dump` already accepted `--dump-manifest` for a real multi-TU extraction;
`--dry-run` already meant "resolve every input, run no compiler, exit before
doing real work" (ADR-043 D9) for every other `dump` input shape. The
standalone `plan` command duplicated exactly that contract for one input
shape only. `cli_dump_helpers.render_dump_dry_run` now accepts the parsed
manifest and, when present, reports its translation units and
`scope_fingerprint` (ADR-050 D1) — computed from the manifest document
alone, no compiler invocation, identically to what `plan` printed.

One behavioral accommodation was needed: `dump`'s ordinary dry-run blocks
(exit 1) when neither `SO_PATH` nor `--sources`/`--build-info` is given
("dump has nothing to analyze"), but the whole point of the former `plan`
command was validating a manifest *before* any artifact exists. A
`--dump-manifest`-only dry run (no `SO_PATH`, no `--sources`/`--build-info`)
now downgrades that case to a warning instead of a blocker — the manifest is
still parsed and fingerprinted successfully, with a note that a real
extraction additionally needs `SO_PATH` (`--dump-manifest` extraction is
only wired for ELF binaries today). This does not change what a `dump
SO_PATH --dump-manifest FILE --dry-run` invocation reports; it only makes
the manifest-only preflight (no `SO_PATH` at all) succeed instead of
blocking, restoring the former standalone command's actual behavior.

**Known gap, accepted rather than worked around: no machine-readable output.**
The former `plan --dump-manifest --format json [-o FILE]` gave automation a
JSON `{manifest, scope_fingerprint}` payload. `dump` has no `--format` option
at all (its real-run output is always the snapshot JSON, with no text mode to
switch away from), and `--dry-run` is universally text-only across *every*
command that supports it (`compare --dry-run --format json` and `scan
--dry-run --format json` also render plain text today, `--format` is silently
inert against `--dry-run` everywhere, ADR-043 D9) — `--dry-run` also
unconditionally rejects `-o`/`--output` (`reject_dry_run_with_output`: "a dry
run performs no analysis and writes nothing"). Special-casing JSON output for
just this one flag combination would reintroduce exactly the kind of
narrow, parallel vocabulary this ADR exists to eliminate elsewhere; giving
every dry-run a `--format json` mode is a real, defensible follow-up but a
separate, general design question (which commands, what shape, does it
apply to blockers/warnings too) that deserves its own review, not a rider on
a CLI-reorganization PR. An automation that specifically needs the manifest
+ fingerprint as JSON without a compiler invocation can call the same two
functions `render_dump_dry_run` does, directly:

```python
from pathlib import Path
from abicheck.dump_manifest import load_manifest
from abicheck.comparability import compute_extraction_contract

manifest = load_manifest(Path("manifest.yaml"))
contract = compute_extraction_contract(
    declared_headers=list(manifest.roots),
    public_header_paths=list(manifest.public_header_paths),
    public_header_dirs=list(manifest.public_header_dirs),
    l2_frontend_ran=False,
)
print(contract.scope_fingerprint if contract else None)
```

### D5. `project plan` is fail-closed on an empty run-plan by default

The former `run-plan generate` treated a run-plan that resolved to zero
`checks[]` as a warning, exit 0 — a legitimate outcome for a config with no
`targets:`/`bundles:` at all, or an implicit profile sweep matching nothing.
But every downstream consumer (a build matrix, `aggregate`) is naturally
gated on "were there any checks," so a silently-empty run-plan makes a CI
workflow report success having gated nothing — the same failure class D13's
`aggregate` coverage gate exists to close for a *missing* target, now
recurring for an *entirely empty* plan.

`project plan` now exits `1` on an empty run-plan unless `--allow-empty` is
passed explicitly. `check-project.yml`'s own `plan` job already carries a
more specific, better-worded guard for this exact case (the dedicated
`no-checks` job, added before this ADR) — it now passes `--allow-empty` to
keep deferring to that existing, more actionable guard rather than stopping
mid-script on the new default. A caller with no guard of its own — the
common case this default is for — gets a hard stop instead of silent
success.

### D6. The admission bar for a future root command is now explicit

To prevent this drift from recurring under a fifth artifact, `AGENTS.md`'s
"Adding a new top-level command" section states the review criteria a new
root command must clear, distilled from this review:

1. It answers a stable, user-facing question, not "here is an artifact,
   expose the function that reads it."
2. Its operand is a domain object a user already thinks in terms of, not an
   internal pipeline transport format.
3. It is useful outside one specific CI Action's wire format — otherwise it
   is a library function an Action/workflow calls directly.
4. It doesn't already fit as an option or subcommand of an existing durable
   operation.
5. It has a real, validated usage scenario beyond the PR introducing it.
6. Landing it updates `tests/test_cli_root_surface.py`, `AGENTS.md`,
   `README.md`, and the generated CLI reference in the same PR.

A command that fails only criterion 2 (multi-target/project-integration
surface whose *question* is real but whose *operand* is still an internal
artifact) is not necessarily rejected outright — it is a candidate
subcommand of the existing `project` group instead of a new root command,
the same way this ADR relocated three of the four commands it removed.

---

## Non-goals

- **Not a rewrite of the ADR-047 project-integration model.** Every concept
  it defines (project, build profile, target, build output, baseline
  channel, check, run plan, report, fan-in) is unchanged; only which
  operations on those concepts are public root CLI commands changes.
- **Not a change to any command's underlying behavior beyond D4/D5 above.**
  `project validate`/`validate-build`/`plan`'s validation logic, exit codes,
  and output shapes are identical to their former `project-targets
  validate`/`build-output validate`/`run-plan generate` selves — only the
  invocation path changed.
- **Not a second external-integration pilot.** The GitHub review that
  prompted this ADR separately noted `check-project.yml`'s artifact-staging
  convention has not yet been exercised end-to-end by a second, external
  consumer repository. That remains open, tracked outside this ADR — it is
  about validating the reusable workflow, not the CLI command shape this
  ADR addresses.

## Consequences

- Positive: the root surface is self-consistent with ADR-043's original
  intent again — every root command answers a distinct user question over a
  domain-shaped operand, and the executable pin
  (`tests/test_cli_root_surface.py`) matches the documented and implemented
  surface simultaneously.
- Positive: `check-project.yml` loses one shell step (`run-plan
  to-aggregate-manifest` → `aggregate --manifest`, now one `aggregate
  --run-plan` call) and one intermediate artifact file
  (`aggregate-manifest.json`).
- Negative (accepted): any external documentation, blog post, or script that
  invoked `build-output`, `project-targets`, `run-plan`, or `plan` directly
  breaks with a plain "no such command" usage error (exit 64) — consistent
  with this repo's pre-1.0, no-alias policy (ADR-043), but a real breaking
  change for anyone who had already adopted the G30 surface between its
  introduction and this ADR.
- Negative (accepted): `dump --dry-run --dump-manifest` with no `SO_PATH` no
  longer signals "nothing to analyze" as a blocker — it is now a legitimate,
  narrower manifest-only preflight. A caller relying on the old blocking
  behavior for that specific input combination (unlikely, since the
  standalone `plan` command already existed for exactly this case) would see
  a warning instead of an exit-1 block.
- Negative (accepted): automation that consumed `plan --dump-manifest
  --format json [-o FILE]`'s machine-readable output has no CLI-level
  replacement — `dump --dry-run` is text-only, matching every other
  command's dry-run (see D4). The two-line Python replacement in D4 covers
  the same computation; a general `--dry-run --format json` capability is
  deliberately left as a separate, future decision (GitHub review, PR #640).

## Relationship to existing ADRs

- **Amends ADR-043 D1/D13** — the root command count and set, again, the
  same way D13 amended D1.
- **Does not amend ADR-047** — the project-integration model it defines is
  unchanged; only the previously-undocumented fact that G30 implementation
  work had grown four separate root command groups is corrected here.
  ADR-047's own CLI-invocation examples predate this consolidation and are
  not exhaustively rewritten; treat this ADR as authoritative for current
  command names.
- **Amends ADR-050 D3** — `plan --dump-manifest` (introduced there) is
  folded into `dump --dump-manifest --dry-run`; the `scope_fingerprint`
  contract D3 defines is unchanged, only its CLI entry point moved.

## References

- ADR-037 (CLI Interface Contract) — the "different question / different
  operand shape" bar for a new root command, applied here in reverse.
- ADR-043 (Pre-1.0 CLI Surface Reset) — the root-collapse precedent and its
  executable pin this ADR keeps honest.
- ADR-047 (GitHub Actions Integration Model) — the project-integration
  domain model whose CLI surface this ADR reorganizes.
- ADR-050 (Comparability Contract and Multi-TU Manifest) — D3's
  `--dump-manifest` diagnostic, folded into `dump --dry-run` here.
