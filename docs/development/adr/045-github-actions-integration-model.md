# ADR-045: GitHub Actions Integration Model — Project Lifecycle Over Aggregate-Centric Design

**Date:** 2026-07-19
**Status:** Proposed — not implemented. This ADR records the target domain
model and component surface; `docs/development/plans/g29-github-actions-integration-model.md`
carries the phased backlog that implements it.
**Decision maker:** Nikolay Petrov

---

## Context

ADR-017 chose a composite action as the GitHub Actions delivery mechanism and
has held up structurally. What has drifted is everything *above* that
mechanism: `action.yml` has grown to 51 inputs across five modes, two more
composite actions (`collect-facts`, `baseline`) were added for source
evidence and snapshot production, and `abicheck aggregate` was added as a
fan-in CLI command. Each addition was locally reasonable; the result read
command-first rather than scenario-first — a project owner had to already
know which CLI mode maps to their build before they could pick an Action
input.

This ADR is scoped to the *integration model*: the domain vocabulary, the
component surface, and the artifacts that flow between jobs. It does not
change detector logic, snapshot schemas, or the core `compare`/`scan`/`dump`
CLI semantics.

### What the audit found

Grounded in reading `action.yml`, `action/run.sh`, `actions/collect-facts/`,
`actions/baseline/`, `abicheck/cli_aggregate.py`, `abicheck/aggregate.py`,
and the doc corpus in `docs/user-guide/`:

1. **`abicheck aggregate` is confirmed fan-in-only.** `abicheck/aggregate.py:16-24`
   states it directly: it "does not analyze a binary — it reconciles
   already-produced reports." It combines worst-verdict, each report's own
   pre-computed gate decision, and target-coverage — it never re-runs a
   comparison. There is no `actions/aggregate` composite action; today it's
   invoked as a raw CLI step in recipe docs. This confirms the premise of
   this ADR: aggregate is one scenario (S28 below), not a load-bearing
   abstraction the rest of the model should be built around.

2. **Root `action.yml` has real per-mode input scoping the schema doesn't
   express.** `debug-info1/2`, `devel-pkg1/2`, `dso-only`,
   `include-private-dso`, `keep-extracted`, `fail-on-removed-library`, and
   `jobs` are declared as unconditional top-level inputs but are only
   forwarded by `run.sh` inside the `_is_release_style_operand()` guard
   (`action/run.sh:387-407`) — i.e. only when `old-library`/`new-library` is
   a directory or package, not a single binary pair. `abi-baseline` is
   resolved unconditionally at `run.sh:150-233` but only consumed by
   `compare`/`scan`; setting it on `mode: dump` silently no-ops with no
   warning. `estimate`/`audit` are scan-only aliases declared generically.
   None of this is a bug — `run.sh` comments document the intent, and
   `action.yml`'s `description:` text for every one of these inputs already
   states its scope inline (e.g. `debug-info1`: "compare mode,
   directory/package operands only"; `abi-baseline`: "for compare mode ...
   or scan mode"; `estimate`/`audit`: "scan mode only" — confirmed by
   re-reading `action.yml`). The remaining gap is narrower than a first
   read suggests: there is no *runtime* signal when one of these is set on
   an incompatible mode — a reader who doesn't check the description text
   gets silent no-op behavior instead of a warning. §"P0" in the companion
   plan scopes this correctly as a runtime-validation item, not a
   documentation rewrite.

3. **`collect-facts`'s `phase: auto` doesn't complete for two of three
   producers.** For `producer: wrapper` or `producer: clang-plugin`,
   `phase: auto` silently only runs `prepare` (`actions/collect-facts/run.sh:714-716`)
   — the caller's own build step still has to run before `verify` means
   anything, so `auto` is only actually a single self-contained step for
   `producer: replay`. This is exactly the kind of implicit two-step
   choreography that S8/S9 need to surface explicitly rather than bury in a
   `phase: auto` default.

4. **Documentation is scenario-adjacent but command-organized.** Multi-DSO
   guidance is split three ways (`github-action.md`, `github-action-recipes.md`,
   `github-action-source-scans.md`) with no single canonical page (confirmed by
   research into `docs/user-guide/*`). The L0–L5 evidence-layer table is
   deliberately restated in five places per `docs/CLAUDE.md`'s own note — by
   design, but it means any layer-model or producer change has a five-file
   blast radius. `docs/user-guide/choose-your-workflow.md` is the closest
   existing thing to a scenario front door and is the right foundation to
   build on, not replace.

5. **One real pilot exists with a written validation report:**
   `validation/pvxs-abi-validation-2026-07.md` (epics-base/pvxs, two
   libraries `libpvxs`/`libpvxsIoc`, Make-based build, no compile DB). It
   found and fixed three real defects (an O(N²) perf bug, RTTI-symbol false
   positives, a zero-config `scan --sources` include-dir bug) and ends with a
   recommended two-library `compare` workflow. **No second pilot with an
   equivalent validation report exists.** A repo-wide search for "Vandal"
   returned zero matches, and oneDAL appears only as a scan-timing data point
   (`validation/uxl-scan-levels-timing-2026-06.md`, built from conda-forge
   release binaries — not a submitted integration PR) and as an illustrative
   example in `multi-binary.md`/`scan-levels.md`, never as a named
   pilot-integration write-up. **This ADR treats PVXS as its only confirmed
   pilot and does not fabricate a second one.** §14 records this as an open
   validation gap rather than inventing findings for a repository that
   cannot be located.

---

## Decision

Reorganize the GitHub Actions integration surface around a **project
integration lifecycle** instead of the CLI command set, with `aggregate`
demoted to one terminal fan-in scenario within that lifecycle:

```text
project configuration
    → existing or instrumented product build
    → build outputs and evidence
    → target discovery/resolution
    → baseline resolution
    → scan/compare/audit          (per resolved target — this is "check")
    → reporting
    → optional fan-in             (aggregate — only when >1 target)
    → baseline publication/refresh
```

Concretely, this means:

- A stable **domain vocabulary** (§1) that every Action input, schema field,
  and doc page reuses instead of ad hoc terms.
- A small number of **primitives** (existing `collect-facts`/`baseline`,
  plus new `resolve-baseline` and `check-target`) that each do one lifecycle
  step, and **reusable workflows** that compose them for the common
  end-to-end paths (§4).
- A **standardized build-output contract** so "build once, check many
  targets" is a real artifact, not an implicit convention (§2, §5).
- A **baseline lifecycle** with two named channels — release-contract and
  accepted-main — resolved fail-loud, never silently degraded to "no
  baseline = compatible" (§2, §6).
- A **report envelope** that separates compatibility, evidence coverage,
  operational status, and policy-gate decision as four distinct fields, so
  `aggregate` (or any consumer) never has to infer one from another (§7).
- **`.abicheck.yml` gains a portable `targets:`/`profiles:` block**; GitHub
  runner/token/artifact concerns stay in workflow inputs, never migrate into
  the portable config (§3, decision D5 below).

---

## 1. Domain model

| Term | Definition | Where it's new vs. existing |
|---|---|---|
| **Project** | A repository or shipped product containing one or more ABI/API contracts. | Implicit today (= "the repo"); made explicit as the top-level config scope. |
| **Build profile** | One ABI-significant build configuration: OS, arch, compiler/toolchain, C++ ABI/stdlib, debug/release, ISA, feature flags — the axis that makes two binaries comparable or not. | New explicit identity. Today only encoded loosely via directory/matrix-lane naming. |
| **Target** | One independently checkable ABI/API contract — usually one shared library, but also a plugin contract, an app-consumer contract, or a build-wide source audit. | New explicit identity. Today conflated with "library" or "the binary passed to `compare`". |
| **Release bundle** | A set of binaries shipped together with cross-library dependencies — the scope `abicheck compare` (directory/package mode) and `--manifest` bundle analysis already operate on (`docs/user-guide/multi-binary.md`). | Existing capability, newly named distinctly from "multiple independent targets" (S14 vs. S15). |
| **Build output** | The standardized, portable artifact a build publishes: binaries, headers, profile identity, commit identity, toolchain provenance, target mapping, compile DB / source facts, digests. | **New** (§2, §5). Closest existing analog is the ad hoc directory a user points `--library`/`--header` at; this makes it a defined, versioned contract. |
| **Source evidence** | L3/L4/L5 evidence from replay/wrapper/plugin (`abicheck/buildsource/`), either build-wide or target-specific. | Existing (`actions/collect-facts`, ADR-028/030/038); this ADR requires every evidence pack to declare which targets it projects onto (§9 — the S16 boundary). |
| **Baseline channel** | Named lifecycle source of a baseline: `release-contract`, `accepted-main`, `explicit` (tag/version), or `custom`. | Existing informally in `docs/user-guide/baseline-management.md`'s "two kinds of baseline"; made a first-class enum here. |
| **Baseline set** | One atomic manifest + one snapshot per target, for one build profile or release bundle. | **New name** for what `actions/baseline`'s `manifest.json` already produces (`actions/baseline/build_manifest.py`) — no code change, just the vocabulary this ADR standardizes on. |
| **Check** | One application of policy to `target × profile × baseline channel × evidence requirement`. | New unit of accounting — today implicit in "one `compare`/`scan` invocation." |
| **Run plan** | The exact, immutable description of which checks a CI run performs. | **New artifact** (§5) — today implicit in workflow YAML + matrix, not machine-readable. |
| **Report** | The result of one check, carrying full identity (target, profile, candidate, baseline, config, commit, evidence depth) — §7. | Existing JSON report, extended with the identity fields §7 requires. |
| **Fan-in** | Combining multiple reports into one CI status. | Existing (`abicheck aggregate`) — explicitly scoped to S28, not the architecture's center. |

### Why these seven boundaries matter (per the task's explicit ask)

The following are easy to conflate and have different failure semantics — a
design built around `aggregate` naturally collapses several of them into
"multiple reports," which loses the distinction:

1. **Multi-binary bundle analysis** (S14) — one `compare`/`--manifest`
   invocation, one report, cross-library findings. A missing library in the
   bundle is itself a finding.
2. **Multiple independent targets** (S15) — N separate checks, N reports,
   each with its own header/compiler context; one target's failure doesn't
   invalidate another's report.
3. **Multiple build profiles** (S17) — the *same* target checked under
   different profiles; a profile is a baseline-set axis, not a target axis.
4. **Multiple baseline channels** (S21) — the *same* target/profile checked
   against two different baselines answering two different questions
   ("did I break the last release" vs. "did I break what main already
   accepted").
5. **Shared source facts** (S16) — one evidence pack, multiple target
   *projections*; a pack is never automatically "for" every DSO in the repo.
6. **Multiple GitHub Actions jobs** — an orchestration/scaling concern
   (matrix, artifact upload/download), orthogonal to all of the above.
7. **Aggregate report** (S28) — a specific fan-in of items 2–4, needed only
   when more than one check's result must produce one CI status.

---

## 2. Standardized build output

`abicheck-build/` (versioned artifact directory), producer-agnostic:

```text
abicheck-build/
  build-output.json          # schema below
  artifacts/                 # binaries as published by the real build
  headers/                   # public header roots, as-installed layout
  generated-headers/         # codegen/configure output, kept separate from headers/
  evidence/
    compile_commands.json    # if produced
    abicheck_inputs/         # source-facts pack, per ADR-028's inputs-pack protocol
  provenance/                # toolchain version dumps, build logs digest, etc.
```

`build-output.json` (schema `abicheck.build-output/v1`):

```json
{
  "schema": "abicheck.build-output/v1",
  "project": "epics-base/pvxs",
  "head_sha": "b7e2c1a...",
  "source_tree_digest": "sha256:...",
  "profile": {
    "id": "linux-x86_64-gcc13-release",
    "os": "linux", "arch": "x86_64",
    "compiler": {"family": "gcc", "version": "13.2.0"},
    "cxx_abi": "itanium", "stdlib": "libstdc++",
    "config": "release"
  },
  "targets": [
    {
      "id": "libpvxs",
      "binary": "artifacts/lib/libpvxs.so.1.5",
      "public_header_roots": ["headers/pvxs"],
      "compile_context": {"include_dirs": ["headers"], "defines": ["PVXS_ENABLE_EXPERT_API"]},
      "bundle": "pvxs-release",
      "evidence": {"kind": "source-facts", "path": "evidence/abicheck_inputs", "projection": "declared"}
    },
    {"id": "libpvxsIoc", "binary": "artifacts/lib/libpvxsIoc.so.1.5", "...": "..."}
  ],
  "bundles": [{"id": "pvxs-release", "targets": ["libpvxs", "libpvxsIoc"]}],
  "evidence_producer": {"kind": "wrapper", "tool": "abicheck-cc", "version": "0.x.y"},
  "digests": {"artifacts/lib/libpvxs.so.1.5": "sha256:..."},
  "diagnostics": {"warnings": [], "skipped_targets": []}
}
```

Design points:

- **`generated-headers/` is separate from `headers/`** so S10 (codegen) can't
  silently claim a `headers/` root that a plain configure step didn't
  actually populate — the build-output *validator* (§11) treats an empty
  `generated-headers/` root declared non-empty in `build-output.json` as a
  hard validation failure, not a warning.
- **`evidence.projection` is `"declared"` or `"inferred"`.** `"declared"`
  means the build itself asserted this evidence pack belongs to this target
  (e.g. per-target compile DB filtering, or a wrapper invoked once per link
  step); `"inferred"` means abicheck derived it from a build-wide pack via
  TU→target mapping and it carries lower confidence — this is the safe
  default for today's real capability (§9) versus the aspirational full
  model.
- **abicheck does not produce `build-output.json` by building the project.**
  A thin `abicheck build-output emit` helper (new, §11) or direct authoring
  is how a project's existing build (or a CMake/Meson `install` step)
  populates it — this is the mechanism for "build once, scan many" (S3)
  without abicheck ever owning the build.
- **One `build-output.json` = one build profile, always — confirmed gap from
  review.** `profile` above is a singular object, not a list, by design: a
  single build produces binaries for exactly one OS/arch/compiler/config
  combination, so one artifact can only ever describe one profile. An
  earlier draft of §8's S17 row said `check-project.yml` "consumes a
  `build-output.json` artifact" (singular) while also matrixing over
  `profiles[]` (plural) with no stated mapping — under-specified, since a
  reader could not tell whether that meant one artifact holding multiple
  profiles (which the schema doesn't support) or something else. **S17's
  actual model:** each build profile in `.abicheck.yml`'s `profiles:` block
  corresponds to its own CI **build job**, each publishing its own
  uniquely-named artifact — `abicheck-build-<profile.id>/` (e.g.
  `abicheck-build-linux-x86_64-gcc13-release/`,
  `abicheck-build-windows-x86_64-msvc-release/`) — so `check-project.yml`'s
  matrix has one cell per `(target, profile)` pair, and each cell downloads
  the *one* `build-output.json` artifact matching its own `profile.id`, not
  a shared artifact it has to disambiguate at runtime. This keeps the
  build-output schema itself unchanged (still one profile per artifact) and
  puts the multi-profile fan-out where it structurally belongs: in the
  artifact-naming/matrix contract, not in `build-output.json`'s shape.

---

## 3. Project contract: extending `.abicheck.yml`, not inventing a manifest zoo

Four config-surface options were compared (§13, decision D5); the chosen
answer is **B+C hybrid**: `.abicheck.yml` gains a portable
`targets:`/`profiles:`/`baseline:` block (stable, project-owned, checked into
the repo), while GitHub-runner-specific and per-run values (candidate
artifact path, current SHA, gate mode, token) stay as **workflow/Action
inputs**, never migrate into `.abicheck.yml`. This avoids both extremes: a
config file polluted with `runs-on`-flavored values, and a second
un-versioned YAML dialect duplicating what `.abicheck.yml` already owns
(policy, suppression, severity).

```yaml
# .abicheck.yml (excerpt — new top-level keys)
targets:
  libpvxs:
    binary_pattern: "lib/libpvxs.so*"
    public_headers: ["headers/pvxs"]
    bundle: pvxs-release
  libpvxsIoc:
    binary_pattern: "lib/libpvxsIoc.so*"
    public_headers: ["headers/pvxsIoc"]
    bundle: pvxs-release

bundles:
  pvxs-release:
    targets: [libpvxs, libpvxsIoc]

baseline:
  channels:
    release-contract: {source: github-release, asset_pattern: "abicheck-baseline-*.tar.zst"}
    accepted-main: {source: actions-cache, key_prefix: "abicheck-baseline-main"}
```

Naming resolution for the four overloaded "manifest" meanings the task
flags — each keeps one unambiguous name, none is called bare `manifest.json`:

| Concept | Canonical name | Existing artifact it maps to |
|---|---|---|
| Bundle cross-library contract | `bundle-contract.yml` / the existing `--manifest` flag to `compare`/`multi-binary` | Already exists (`docs/user-guide/multi-binary.md`'s `--manifest`); flag name unchanged, doc term clarified. |
| Baseline-set descriptor | `baseline-set.json` | `actions/baseline/build_manifest.py`'s `manifest.json` — renamed in docs/new schema id only, existing filename kept for compat. |
| Aggregate expected-target set | `abicheck aggregate --manifest` (unchanged CLI flag) / doc term "target-manifest" | Existing `cli_aggregate.py` flag. |
| Build evidence pack descriptor | `build-output.json` (§2) | New. |

---

## 4. Component surface

### Low-level primitives (kept, one gains a sibling)

| Action | Responsibility | Status |
|---|---|---|
| `actions/collect-facts` | Prepare/verify source evidence for one producer (replay/wrapper/clang-plugin). Does not decide project topology. | Existing — kept as-is; `phase: auto`'s two-producer partial-completion (finding 3 above) gets a fail-loud diagnostic, not silent truncation (P0 item). |
| `actions/baseline` | Produce one baseline set (snapshot + `baseline-set.json`) from resolved targets. Read-only: never commits/pushes (already true — `actions/baseline/action.yml:6-8`). | Existing — kept as-is; consumes the new `targets:` block from `.abicheck.yml` instead of a flat `libraries:` list where available. |
| `actions/resolve-baseline` | Resolve `channel × target × profile` → one baseline snapshot path, checking schema/digest/config-identity/evidence-producer compatibility; distinguishes not-found / ambiguous / wrong-profile / stale-schema / incompatible-evidence and never turns any of those into a compatibility verdict. | **New** — see rationale below. |
| root `action.yml` | Execute one `compare`/`dump`/`scan`/`deps-tree`/`deps-compare` invocation. | Existing, unchanged surface; input-scoping documentation fixed per finding 2 (P0), not restructured. |
| `actions/check-target` | Compose `resolve-baseline` + root action + `collect-facts` (if evidence required) for **one resolved target**; always emits the report envelope (§7); accepts `gate-mode: local\|deferred\|advisory`. | **New** — the single high-level primitive the task's "smaller surface" option asks to evaluate; adopted (see decision D6). |
| — (no dedicated Action) | Fan-in. | `abicheck aggregate` stays a plain CLI step invoked from the `check-project` reusable workflow (§ below) — a dedicated `actions/aggregate` composite adds no value over one `run:` line, since aggregate's job is a single CLI call with no shell orchestration to hide. |

**Why `resolve-baseline` is a new primitive, not folded into `check-target`
or the root action:** every one of S2/S19/S20/S21's failure modes is a
baseline-resolution failure, and today that logic is inlined and duplicated
inside `action/run.sh:150-233` (the `abi-baseline` resolution block) with no
independent success/failure signal a caller can branch on. Separating it
lets `check-target` treat "baseline not found" as a distinct, typed
condition instead of falling through to whatever `compare`'s own
missing-file error text happens to be.

### Reusable workflows

| Workflow | Composes | Primary scenarios |
|---|---|---|
| `check-single.yml` | a single `check-target` call (one target, one profile) — `check-target` owns baseline resolution internally, see below | S1, S2, S5, S6 |
| `check-project.yml` | consumes a `build-output.json` artifact → dynamic matrix over `targets[]`/`profiles[]` → `check-target` per cell → optional `aggregate` job if `>1` cell | S3, S14 (via one `check-target` call per bundle), S15, S17, S25, S28 |
| `publish-baseline.yml` | build/consume `build-output.json` → `actions/baseline` → upload as release asset (atomic archive, §10) | S19 |
| `update-main-baseline.yml` | same as above, targeting the `accepted-main` channel storage backend, triggered on default-branch push | S20 |

**Resolution ownership, made explicit (review caught this was ambiguous in
an earlier draft):** `resolve-baseline` is invoked exactly once per check,
*inside* `check-target` (§4's primitive table already states `check-target`
"composes root `action.yml` + `collect-facts` ... + `resolve-baseline`").
Neither reusable workflow calls `resolve-baseline` a second time at the
workflow level — `check-single.yml` and `check-project.yml`'s matrix cells
each call `check-target` once and get an already-resolved baseline as part
of that one call. A caller who needs the resolved baseline path *outside*
`check-target` (e.g. to display it in a workflow-level summary before the
check runs) reads it back from `check-target`'s own output, not by invoking
`resolve-baseline` separately.

`check-packages.yml` was considered and **rejected as a fifth workflow**
(decision D7): a package/prebuilt-artifact target (S13) is just another
`build-output.json` producer (a thin adapter unpacks RPM/Deb/tar/conda into
the same `artifacts/`/`headers/` layout) — it reuses `check-project.yml`
rather than duplicating matrix/aggregate logic.

Composite Actions structurally cannot create jobs or a dynamic matrix
(confirmed against current `action.yml`/`actions/*/action.yml`, which are
plain `runs.using: composite` — no `jobs:` key is a valid composite-action
key); this is why `check-project.yml` must be a reusable *workflow*, not a
fourth composite action, whenever more than one target/profile is in play.

---

## 5. Run plan

`run-plan.json` (schema `abicheck.run-plan/v1`) — the machine-readable
output of resolving `.abicheck.yml` + dynamic CI inputs into an exact set of
checks, generated once per `check-project.yml` invocation and consumed by
the matrix directly, and by `aggregate`'s `--manifest` only after the
required projection described immediately below (not passed to `aggregate`
as-is):

```json
{
  "schema": "abicheck.run-plan/v1",
  "project": "epics-base/pvxs",
  "head_sha": "b7e2c1a...",
  "checks": [
    {"target": "libpvxs", "profile": "linux-x86_64-gcc13-release",
     "baseline_channel": "accepted-main", "required": true, "evidence_depth": "headers"},
    {"target": "libpvxsIoc", "profile": "linux-x86_64-gcc13-release",
     "baseline_channel": "accepted-main", "required": true, "evidence_depth": "headers"}
  ]
}
```

This is the artifact `check-project.yml`'s matrix step reads to fan out.
**Correction from an earlier draft:** `run-plan.json`'s `checks[]` schema is
*not* wire-compatible with `abicheck aggregate --manifest` as it exists
today — confirmed by reading `abicheck/aggregate.py:753-769`
(`ExpectedTargets.from_manifest_data`), which requires a top-level
`{"targets": [{"id", "required"}]}` shape and raises `AggregateError`
("manifest 'targets' must be a non-empty list") on anything else, including
a `checks[]`-shaped document. `run-plan.json` is deliberately richer than
that format (it carries `profile`/`baseline_channel`/`evidence_depth` per
check, which `aggregate` has no use for — it only needs to know which
target IDs are required). So `check-project.yml`'s aggregate step must
*project* `run-plan.json` down to the existing `{"targets": [...]}` shape
rather than pass `run-plan.json` straight through as `--manifest`.

**Second correction, also from review:** that projection is not the trivial
one-line rename it first looks like once S17 (multiple profiles) or S21
(multiple baseline channels) are in play. `abicheck/aggregate.py:642-729`
(`collect_reports`) keys every loaded report strictly by `target_id` — read
from the report's own `target_id` field, falling back to the report
filename — and **hard-errors** (`AggregateError: duplicate target id`) the
moment two reports resolve to the same `target_id`. A project with `libfoo`
checked on two profiles, or on both `release-contract` and `accepted-main`
simultaneously, produces two reports for one bare target name — exactly the
collision `collect_reports` rejects. The manifest projection therefore must
use each check's full `check_id` (§7's `target@profile#baseline_channel`
form) as the manifest `targets[].id`, **and** `check-target` (P1.3) must
write that same `check_id` into each report's own `target_id` field.

**Third correction, from a follow-up review pass:** this must be
**unconditional — every check, not just checks sharing a target with
another check.** An earlier draft scoped the `target_id = check_id` rule to
"whenever a project has more than one check per target," which is a real
bug: `abicheck/aggregate.py`'s matching is an exact string comparison
(`found.get(tid)` against the manifest's `targets[].id`, §"P1.3/P1.4"
below). If the manifest projection always emits `check_id`-shaped IDs (which
it must, to stay one consistent rule) but `check-target` only populates
`target_id` with `check_id` for the multi-check case, then an *ordinary*
single-target, single-profile, single-channel check (S1–S15's majority
case, including PVXS/S15 itself) reports `target_id: "libpvxs"` while the
manifest expects `"libpvxs@linux-x86_64-gcc13-release#accepted-main"` —
an exact-match miss, so `aggregate` reports the required target *missing*
and the real report *unexpected*, on the single most common flow this whole
model is meant to make simple. There is no conditional case here: every
`check-target` run writes `target_id = check_id` into its report, full
stop, and every manifest projection uses `check_id` as `targets[].id`, full
stop — the "simple case looks the same" property comes from `check_id`
always being a stable, well-formed string (never from sometimes being the
bare target name), so `aggregate`'s exact match always lines up. This is
what P1.3/P1.4's companion plan entries now specify. Coverage is still
checked against the same explicit plan, not an implicit job list — the fix
is in how identity flows between the two artifacts, not in the coverage
guarantee itself.

---

## 6. Baseline lifecycle

Two named channels, each with distinct semantics (existing informal
distinction in `baseline-management.md`, made structural):

- **`release-contract`** — immutable; built from a shipping-equivalent
  build (ideally the *same* `build-output.json` the release itself
  publishes, not a second divergent build); published as one atomic
  baseline-set archive (`baseline-<profile>.tar.zst` containing
  `baseline-set.json` + one snapshot per target, mirroring the task's
  proposed layout); changes only on release.
- **`accepted-main`** — mutable; refreshed by `update-main-baseline.yml` on
  every default-branch push; answers "did this PR introduce a break vs. what
  main already accepted," never substitutes for `release-contract`.

`resolve-baseline` failure taxonomy (all fail-loud, never silently
degraded to a *compatibility* verdict — but "fail-loud" and "advisory" are
not the same thing, and an earlier draft of the `not_found` row conflated
them; corrected below per review):

| Condition | Resolver outcome | What the check does |
|---|---|---|
| No baseline set exists for `channel` yet, and this check's `run-plan.json` entry has `required: false` (explicit bootstrap opt-in — e.g. the very first `release-contract` publish, before any release exists) | `not_found` (bootstrap) | Advisory pass with an explicit "no baseline yet" report field — never a compatibility verdict. |
| No baseline set exists for `channel` yet, and the check is `required: true` (the default) | `not_found` (required) | **Hard operational failure**, exit non-zero. A typo in the channel name, a missing release asset, or a cache-resolution bug must never produce a green branch-protection status with zero comparison performed — `not_found` on a required check is exactly the silent-shallow-success failure mode this ADR exists to eliminate, so it does not get an advisory carve-out by default. |
| Baseline set exists but this target isn't in it | `ambiguous` (target missing from set) | Coverage failure, distinct from a compatibility break. |
| Baseline set is for a different `profile.id` | `wrong_profile` | Hard failure — never silently compare across profiles. |
| `baseline-set.json` schema version newer/older than resolver understands | `stale_schema` | Hard failure with an upgrade-path message. |
| Baseline's `evidence_producer` incompatible with candidate's (e.g. wrapper vs. replay) | `incompatible_evidence` | Hard failure — evidence-producer mismatch is an infrastructure problem, not an ABI finding (S16/S8/S9 boundary). |

---

## 7. Report envelope

Every check's report gains these identity/status fields (existing JSON
report body is additive-compatible — this is new required metadata, not a
schema break to detector output):

```json
{
  "report_schema": "abicheck.report/v1",
  "check_id": "libpvxs@linux-x86_64-gcc13-release#accepted-main",
  "target_id": "libpvxs@linux-x86_64-gcc13-release#accepted-main",
  "project": "epics-base/pvxs",
  "target": "libpvxs",
  "profile_id": "linux-x86_64-gcc13-release",
  "head_sha": "b7e2c1a...",
  "base_ref": "main",
  "candidate_digest": "sha256:...",
  "baseline_channel": "accepted-main",
  "baseline_digest": "sha256:...",
  "requested_depth": "source",
  "effective_depth": "headers",
  "evidence_coverage": {"state": "degraded", "reasons": ["wrapper_pack_empty_for_target"]},
  "compatibility_verdict": "breaking",
  "policy_gate_decision": "fail",
  "operational_errors": [],
  "publication": {"state": "published", "channels": ["job_summary", "pr_comment"]},
  "tool_version": "abicheck 0.x.y",
  "action_version": "abicheck/abicheck@v1"
}
```

**`target_id` is not redundant with `check_id`/`target` — it exists solely
so `aggregate` reads the right value** (a second review catch, from
`chatgpt-codex-connector`): `abicheck/aggregate.py`'s `_load_report_file`
reads `data.get("target_id")` specifically (not `check_id`, not `target`)
when deciding which key to collect a report under, falling back to the
report's filename only if `target_id` is absent. §5's fix requires
multi-profile/multi-channel reports to key by `check_id`, so P1.3's
`check-target` implementation must populate this exact field — `target_id`
— with the `check_id` value, not rely on `check_id` alone being present and
not rely on artifact/filename naming to carry that identity implicitly.
`target` stays the plain, human-readable library name (`libpvxs`) for
display; `target_id` is `aggregate`'s only working input for identity.

Five axes kept explicitly distinct, per the task's requirement (§11 there):
**compatibility** (`compatibility_verdict`), **evidence coverage**
(`evidence_coverage`), **operational status** (`operational_errors` — empty
means clean; `verdict: "ERROR"`-class failures populate it, mirroring how
`abicheck/aggregate.py` already special-cases `verdict == "ERROR"` as an
operational, not compatibility, signal), **policy gate**
(`policy_gate_decision`), and **report publication** — the field added
above, `publication.state` (`published`/`skipped`/`failed`) plus which
channels actually received it. Making this a field, not an implicit
inference from "a report file exists," matters because a report can be
fully computed and still fail to *publish* (PR-comment API error, SARIF
upload rejected) — a downstream consumer must be able to tell "no
publication happened" apart from "no report was produced at all," which an
absent-report inference cannot distinguish. `requested_depth != effective_depth` is
always surfaced — a request for `source` depth that only achieved `headers`
must never render as an unqualified "source-depth check passed."

`gate-mode` replaces the ad hoc combination of `fail-on-breaking` /
`fail-on-api-break` for the new primitives (root `action.yml`'s existing
flags are kept for backward compatibility — see migration mapping below):

- **`local`** — this one target's check sets the job's own exit code (today's
  root-action behavior; correct for S1/S2/S4/S6).
- **`deferred`** — report is always produced; the *matrix's* final
  `aggregate` job computes gate status. Operational/config errors still fail
  the individual job — `deferred` only defers the *compatibility* verdict's
  effect on exit code, never operational errors (S15/S28). **Required
  workflow-contract detail, added per review:** because an individual
  matrix cell is allowed to fail its own job (that's the point — an
  operational error must be visible), `check-project.yml`'s trailing
  `aggregate` job **must** run with `if: always()` (or the equivalent
  `!cancelled()` / needs-result-bucket condition), never a bare `needs:` with
  no `if:`. Plain GitHub Actions `needs:` semantics skip a dependent job when
  any of its dependencies fail, and a *skipped* job reports status
  `success` to branch protection — so without an explicit `if: always()`,
  one matrix cell's operational failure would skip the aggregate job
  entirely and the required branch-protection check would go *green*
  despite a missing/failed target, exactly the "missing required report
  silently becomes compatible" failure mode this ADR exists to close. This
  is not implementation-detail trivia; it is a load-bearing correctness
  requirement of the `deferred` gate-mode contract and is now specified as
  such in `check-project.yml`'s definition (companion plan, P1.4).
- **`advisory`** — report published, findings never affect exit code
  (shadow-rollout burn-in, S26).

Migration mapping: `fail-on-breaking: true` + `fail-on-api-break: false`
(root action's current default) ≡ `gate-mode: local` with the existing
severity thresholds unchanged; `check-target`'s `gate-mode` is additive, not
a breaking change to the root action's existing flags.

---

## 8. Condensed scenario catalog (S1–S28)

Full 15-field cards for all 28 scenarios would run several thousand lines;
this ADR keeps the domain-model decisions above scenario-anchored and
defers exhaustive per-scenario cards (user story, copy-pasteable YAML,
acceptance criteria) to
`docs/development/plans/g29-github-actions-integration-model.md` §Scenario
backlog, where each scenario becomes a tracked, independently
implementable/testable unit. Table below maps each scenario to the primary
workflow/primitive from §4 and its baseline requirement, confirming no
scenario requires `aggregate` except S28.

| # | Scenario | Primary entry point | Baseline | Notes |
|---|---|---|---|---|
| S1 | Single library, committed baseline | `check-single.yml` | explicit (committed file) | Minimal YAML; root action alone suffices, no new primitive needed. |
| S2 | Single library, latest-release baseline | `check-single.yml` | release-contract | Needs `resolve-baseline`'s fail-loud `not_found` handling. |
| S3 | Reuse existing expensive build | `check-project.yml` | either | The `build-output.json` consumer path; no rebuild inside abicheck. |
| S4 | Build+check in one job | `actions/check-target` used as a **step** inside the caller's own job, *not* `check-single.yml` | either | Small-project shortcut; not the default for large repos. Correction from an earlier draft: GitHub's own reusable-workflow docs (`jobs.<job_id>.uses`) confirm a reusable workflow always runs as a separate job with its own runner/workspace, so it structurally cannot share a filesystem with a build step that ran earlier in the caller's job — `check-single.yml` is therefore the wrong entry point for "build and check in one job." S4's real entry point is `actions/check-target` invoked directly as a step (`uses: abicheck/abicheck/actions/check-target@vN`) right after the project's own build steps in the same job, giving it direct access to the just-built artifacts on disk. `check-single.yml` stays correct for S1/S2/S5/S6, where the candidate binary is a git-committed/downloaded artifact and no in-job build step needs to be shared. |
| S5 | Single-build audit, no baseline | `check-single.yml` (`baseline: none`) | none | Advisory by default (§7 `local` vs `advisory`). |
| S6 | Header-aware compatibility | `check-single.yml` | any | Public-header floor; `evidence_coverage` must confirm header parse reached (finding-driven — no silent L0 fallback). |
| S7 | Source scan via compile-DB replay | `check-single.yml`/`check-project.yml` + `collect-facts producer: replay` | any | PR = changed-TU scope; nightly/release = full unseeded. |
| S8 | Source facts via `abicheck-cc` wrapper | `collect-facts producer: wrapper` (prepare) → real build → (verify) | any | Two-step; `phase: auto` limitation (finding 3) documented, not hidden. |
| S9 | Source facts via Clang plugin | `collect-facts producer: clang-plugin` | any | Opt-in optimization, not onboarding default (LLVM-major coupling). |
| S10 | Generated headers / codegen-before-scan | `build-output.json`'s `generated-headers/` root | any | Empty-but-declared root is a hard validation failure (§2). |
| S11 | Make/EPICS/custom build | `collect-facts producer: wrapper` over Make `CC=`/`CXX=` | any | The PVXS validated path (`validation/pvxs-abi-validation-2026-07.md`). |
| S12 | Bazel/sandboxed build | `build-output.json` populated from `aquery`/declared outputs | any | Sandbox side effects must be declared artifacts, not filesystem scraping. |
| S13 | Package-only / prebuilt artifacts | `check-project.yml` via a package→`build-output.json` adapter | any | No source checkout required; folds into `check-project`, no separate workflow (D7). |
| S14 | Multi-DSO release bundle | one `check-target` over the bundle (directory/`--manifest` compare) | any | One report; distinct from S15. |
| S15 | Multiple independent targets, one build | `check-project.yml` matrix, no fan-in required unless gating jointly | any | oneDAL/PVXS-class; each target keeps its own header/compiler context. |
| S16 | Shared source facts, multiple DSO | `collect-facts` + declared `evidence.projection` in `build-output.json` | any | Safe-model-now vs. full-model documented in §2/§9; never auto-assumed ownership. |
| S17 | Multiple build profiles | `check-project.yml` matrix cells keyed by `profile.id`, one per-profile `build-output.json` artifact per cell (§2's "one artifact = one profile" design point) | per-profile | Which lanes are ABI contracts vs. test-only is an explicit `.abicheck.yml` `profiles:` allowlist, not "every CI lane." |
| S18 | Cross compilation | `build-output.json` authored on build host, `check-target` run offline/elsewhere | any | No host auto-detection of target context; dump-producer and compare are decoupled steps. |
| S19 | Release-contract baseline | `publish-baseline.yml` | — (producer) | See §6. |
| S20 | Accepted-main baseline | `update-main-baseline.yml` | — (producer) | See §6. |
| S21 | Multiple baseline channels at once | two `check-target` calls (same target/profile, different `baseline_channel`) | both | Two distinct `check_id`s in the PR UI, never one ambiguous "ABI Check." |
| S22 | Application compatibility (`--used-by`) | `check-target` with an app-consumer target kind | any | App-scoped verdict kept a distinct report field from the library's own verdict. |
| S23 | Plugin/dlopen/dlsym contract | `check-target` with a contract-file target kind | any | Not a public-header compare; a distinct target type in `.abicheck.yml`. |
| S24 | Dependency/container/rootfs checks | root action `deps-tree`/`deps-compare` modes, unchanged | n/a | Explicitly not modeled as a library baseline scan. |
| S25 | Monorepo / multiple components | `check-project.yml` with changed-component-filtered `run-plan.json` | per-component | Run plan may be filtered by diff, but required-target coverage stays fail-closed (§5). |
| S26 | Shadow rollout / migration from another tool | `check-single.yml`/`check-project.yml` with `gate-mode: advisory` | any | Old tool kept running in parallel until acceptance criteria met; no forced removal. |
| S27 | Intentional breaking change | unchanged check, PR-scoped gate relaxation only | accepted-main updates post-merge | Report stays visible; `release-contract` channel is untouched by the relaxation. |
| S28 | Multi-target fan-out/fan-in | `check-project.yml`'s trailing `aggregate` job | n/a (consumes prior checks) | The only scenario `abicheck aggregate` is the entry point for. |

---

## 9. Source evidence: safe model now vs. full model later

Per the task's explicit instruction not to assume a shared source surface
belongs to every DSO: this ADR adopts the **safe model now** as the P0/P1
contract and defers the **full model** to P2 (tracked in the companion plan,
§ "P2").

**Safe model (adopt now):** an evidence pack's `abicheck_inputs/` content is
either (a) target-specific (one wrapper/plugin invocation scoped to one
link unit, or a compile-DB filtered to one target's TUs — `projection:
"declared"`), or (b) explicitly build-wide, in which case it feeds only
build-wide source audits (S5-class checks) and per-target *header* scans,
never an unqualified per-target *source*-depth claim. `build-output.json`'s
`evidence.projection` field (§2) is what a target-specific `check-target`
run inspects before claiming `effective_depth: source` in its report (§7).

**Full model (P2, not built here):** TU → object/link-unit → output-DSO
attribution via linker command/output identity, so a build-wide pack can be
safely and automatically projected onto the correct subset of targets.
Requires linker-invocation capture (`abicheck/buildsource/build_query.py`
already has partial zero-config compile-DB inference for CMake/Bazel/Make
this could extend) — scoped as its own follow-up ADR when undertaken, not
retrofitted into this one.

---

## 10. Baseline storage backends compared

| Backend | Atomic set? | Write access needed | Recommended for |
|---|---|---|---|
| GitHub Release asset | Yes (single tarball upload) | `contents: write` on a release, not a branch | `release-contract` (S19) — matches "publish atomically" requirement. |
| Actions cache | Yes (single cache key) | none (cache API only) | `accepted-main` (S20) — cheap, no push, naturally ages out. |
| git-committed | No (per-file commits) unless staged via a PR | `contents: write` to a branch | S1's minimal single-library case only; **must go through a PR**, never a direct push to a protected branch (security requirement, §12 below). |
| External object store | Yes | store-specific credentials | Large fleets / long retention; out of scope for P0/P1, noted for P2. |

Direct-commit-to-`main` is explicitly **not** the default `accepted-main`
update path for any backend that supports Actions cache or Releases — only
the git-committed backend needs a write, and that write goes through a PR
opened by the workflow, matching the task's "no direct push to protected
main as required path" acceptance criterion.

---

## 11. Validators (fail-loud, no silent shallow success)

Three new validation points, all hard failures (not warnings) when tripped:

1. **`build-output.json` validator** — every declared `headers/`/
   `generated-headers/` root is non-empty; every `targets[].binary` exists
   and its digest matches `digests{}`; `evidence.projection` is consistent
   with what's actually in `evidence/abicheck_inputs/` (non-empty TU count
   for `"declared"`).
2. **Requested-vs-effective depth gate** — reuses the mechanism PR #601
   introduces at the CLI layer (`DumpDepthNotSatisfiedError`, per this
   repo's Known Gaps section) but applied at `check-target` level so a
   `required-depth: source` check that only achieved `headers` is a hard
   failure of that check, never a silently-downgraded pass. This directly
   extends the acknowledged gap rather than duplicating a second enforcement
   path.
3. **`resolve-baseline` taxonomy** (§6 table) — every failure mode has a
   distinct, tested exit condition; none of them may exit 0/"compatible." The
   one documented exception is the §6 bootstrap row (`not_found` on a check
   explicitly marked `required: false`), which exits 0/"advisory" —
   deliberately *not* "compatible" — precisely so a first-ever
   `release-contract` publish isn't blocked by "no baseline exists yet." This
   is a narrow, explicit opt-in (a check must set `required: false` to get
   it), not a general relaxation of the fail-loud rule: every other resolver
   outcome, and every `required: true` check (the default), stays a hard
   failure with no exit-0 path.

---

## 12. Security and reproducibility

- All new composite Actions/reusable workflows referenced from documentation
  examples use the same pinning discipline already established for
  elevated-permission workflows (AGENTS.md's "Action pinning is deliberately
  partial" note) — `check-target`/`resolve-baseline` carry no elevated
  permissions themselves (they read artifacts and write job outputs only),
  so tag-pinning is acceptable there, matching the existing policy's
  blast-radius reasoning; `publish-baseline.yml`/`update-main-baseline.yml`
  (which write releases/caches) get the same SHA-pinning bar as the existing
  `security.yml`/`publish.yml` gate.
- Fork PRs: `check-single.yml`/`check-project.yml` run under `pull_request`
  (not `pull_request_target`) by default in every example, so fork PRs never
  get write-scoped tokens; baseline-publishing workflows are `push`/
  `workflow_dispatch`-triggered only, never PR-triggered.
- `resolve-baseline` treats a baseline produced by a different tool/scanner
  version as `incompatible_evidence` unless explicitly allowlisted — this
  closes the "producer/scanner/aggregator version desync" class of finding
  the task calls out.
- Report `candidate_digest`/`baseline_digest` fields (§7) let a consumer
  detect a stale artifact from a previous run before trusting the verdict.

---

## 13. Decision log

**D1 — Center the model on the project lifecycle, not `aggregate`.**
Alternative: keep extending `abicheck aggregate` and the root action's input
surface incrementally. Rejected because the audit (finding 1) confirms
`aggregate` structurally cannot represent single-target, single-build, or
baseline-lifecycle scenarios — it consumes reports, it doesn't produce
checks. Recommendation: lifecycle-first, as decided above.

**D2 — New `resolve-baseline` primitive vs. folding into `check-target`.**
Alternative: keep baseline resolution inlined in each consumer, as
`action/run.sh:150-233` does today. Rejected because every S2/S19/S20/S21
failure mode is a resolution failure with no independent typed signal today
— duplicating that logic into `check-target` would recreate the same
untestable inline branch it's meant to replace. Recommendation: separate
primitive, per §4.

**D3 — Reusable workflows for matrix/fan-out, not more composite Actions.**
Alternative: a fourth+ composite action attempting matrix-like behavior via
nested `run:` loops. Rejected: composite actions cannot create GitHub jobs
(confirmed — no `runs.using: composite` schema supports `jobs:`), so any
dynamic matrix needs a reusable *workflow*. Recommendation: §4's four
reusable workflows, no composite-action alternative considered viable.

**D4 — Minimal primitive count: evaluated "one `check-target` + two
workflows" per the task's explicit prompt.** Considered collapsing
`resolve-baseline` into `check-target` and cutting `check-single.yml` (fold
into `check-project.yml` with a one-cell matrix). Rejected the full
collapse: `check-single.yml` is the *entire* onboarding experience for S1
(the single most common case) and a one-cell matrix workflow imposes matrix
overhead (artifact upload/download round-trip) on the simplest scenario
users will hit first. Kept `resolve-baseline` separate per D2. Net surface:
5 primitives (3 existing + 2 new) + 4 reusable workflows — smaller than a
literal per-scenario Action count (would be 28), larger than the task's
minimal-floor suggestion, justified by D2/D3's failure-mode reasoning.

**D5 — `.abicheck.yml` extension (B+C hybrid) vs. a new project-schema
file.** Compared all four options the task lists. Rejected "A: everything in
`.abicheck.yml`" because GitHub-runner-specific dynamic values (candidate
artifact path, SHA, token) don't belong in a portable, checked-in config —
they'd force a config edit on every CI run. Rejected "D: new versioned
project schema" as an unjustified second file format duplicating
`.abicheck.yml`'s existing policy/suppression sections. Chose B+C: portable
`targets:`/`profiles:`/`baseline:` block added to `.abicheck.yml` (B), plus
the separate, build-produced (not hand-authored) `build-output.json` (C) —
because build-output is generated data with a different lifecycle (one per
CI run) from project config (one per repo, hand-edited).

**D6 — Adopt `check-target` as the single new high-level primitive.**
Directly answers the task's "consider one `check-target` primitive plus two
reusable workflows" prompt: adopted `check-target` as proposed, but kept
four reusable workflows (not two) because `publish-baseline.yml`/
`update-main-baseline.yml` have materially different triggers (release vs.
default-branch-push) and write-permission scopes that would otherwise force
a workflow-level conditional on write access inside one shared workflow —
judged a worse blast-radius/readability trade than two small workflows.

**D7 — No `check-packages.yml`.** A package-only target (S13) differs from a
source-build target only in how `build-output.json` gets populated (a thin
unpack-adapter vs. a real build). Giving it a fifth workflow would duplicate
`check-project.yml`'s matrix/aggregate logic for no behavioral difference.
Rejected a dedicated workflow; the adapter is a P1 backlog item (companion
plan), not a new workflow.

**D8 — Safe vs. full source-evidence-projection model (S16).** Adopting the
full TU→link-unit→DSO attribution model now was rejected as premature: it
requires linker-invocation capture not yet built (`build_query.py` has only
partial zero-config compile-DB inference today), and shipping an
"automatic" projection before that exists would recreate exactly the
"silent shallow success" failure mode this ADR is meant to eliminate.
Adopted the safe, explicitly-declared-or-build-wide model now (§9), deferred
full attribution to P2.

**D9 — No second pilot repository was fabricated.** The task named a
"Vandal" repository and floated oneDAL PR #3693 as possible second pilots.
Neither has a locatable validation record in this repository (finding 5).
Rather than inventing acceptance findings for an unconfirmed integration,
this ADR records PVXS as the sole confirmed pilot and lists acquiring a
second real pilot's validation report as a P1/P2 backlog item (companion
plan, "Pilot validation gap").

---

## 14. Known gaps this ADR does not close

- **No second validated pilot** (D9) — a real risk to the "vendor
  toolchain / multiple DSO / multiple baseline channels" claims in §8
  (S9/S15/S21) staying aspirational until one exists.
- **Full source-evidence TU→DSO attribution** (§9, D8) is P2, not built
  here; S16's "required full model" is a documented target state only.
- **`build-output.json` producer tooling** (a real `abicheck build-output
  emit` helper, or documented hand-authoring path for CMake/Meson/Bazel/Make)
  does not exist yet — this ADR specifies the schema and consumer contract;
  the companion plan tracks building the producer side.
- This ADR does not itself change `action.yml`, `action/run.sh`,
  `abicheck/cli_aggregate.py`, or any schema file — see the companion plan
  for the PR sequence that implements it.

---

## Consequences

**Positive:** a project owner can now describe their situation in the
domain vocabulary above and land on exactly one canonical entry point
(§4/§8), instead of reverse-engineering which of five CLI modes and 51
inputs applies. `aggregate` stops being an implicit architectural center,
which removes the temptation to route single-target scenarios through it.
The report envelope (§7) gives every downstream consumer (PR comment, SARIF,
branch protection, `aggregate` itself) one unambiguous identity/status
contract instead of inferring it from verdict text.

**Negative / cost:** two new composite Actions, four new reusable
workflows, three new JSON schemas, and a `.abicheck.yml` schema extension are net
new maintenance surface. The scenario-first documentation reorganization
(companion plan) touches most of `docs/user-guide/`. None of this is free,
and it is sequenced in the companion plan specifically so it lands as
independently reviewable PRs rather than one large rewrite.
