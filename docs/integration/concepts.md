---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - project-integration
lifecycle: active
generated: false
---

# Integration Concepts

This page is the glossary behind [Project Integration](index.md) and
[ADR-047](../contribute/adr/047-github-actions-integration-model.md)'s
domain model — the vocabulary every scenario, reusable workflow, and Action
in this section uses consistently. If you only remember one sentence: a
project is checked as one or more **checks**, and a check is one application
of policy to `target × profile × baseline channel × evidence requirement` —
not one implicit `aggregate` report standing in for all of it.

## Project

A repository or shipped product containing one or more ABI/API contracts.
Today this is implicit ("the repo"); `.abicheck.yml`'s `targets:`/`bundles:`/
`profiles:`/`baseline:` block ([Project Targets Schema](../reference/project-targets-schema.md))
makes it the explicit top-level config scope everything else below is scoped
under.

## Build profile

One ABI-significant build configuration: OS, architecture, compiler/
toolchain, C++ ABI/standard library, debug/release, ISA, feature flags — the
axis that determines whether two binaries are even comparable in the first
place. A project with more than one profile (e.g. Linux/GCC release and
Windows/MSVC release) needs a separate baseline per profile; comparing across
profiles is never valid and `resolve-baseline` rejects it outright
(`wrong_profile`, see [ADR-047 §6](../contribute/adr/047-github-actions-integration-model.md#6-baseline-lifecycle)).
Declared under `.abicheck.yml`'s `profiles:` block
([Project Targets Schema](../reference/project-targets-schema.md)); `contract:
true` (the default) marks a profile as an ABI contract that gets a baseline
and gates CI, `contract: false` marks a test-only lane that never does.

## Target

One independently checkable ABI/API contract. Usually one shared library, but
also an application-consumer contract (does a specific app still work against
this library — S22) or a plugin/`dlopen` contract (does a specific set of
required symbols still exist — S23). Declared under `.abicheck.yml`'s
`targets:` block, discriminated by `kind: library|app-consumer|plugin-contract`
([Project Targets Schema](../reference/project-targets-schema.md)). An
app-consumer/plugin-contract target's baseline and candidate lookups redirect
through its own `library:` field (the real library it's scoped against), while
its own id stays the check's reporting identity.

## Release bundle

A set of binaries shipped and versioned together, with cross-library
dependencies — the scope `abicheck compare` (directory/package mode) and
`--instantiation-manifest` bundle analysis already operate on
([Multi-Binary Releases](../use/multi-binary.md)). Deliberately named
distinctly from "multiple independent targets" (bundle = S14, one report with
cross-library findings; independent targets = S15, N separate reports).
Declared under `.abicheck.yml`'s `bundles:` block, referencing member target
ids; a library target can be both a bundle member and independently checked
on its own (`bundle_only: false`, the default).

## Build output

The standardized, portable artifact one build produces: binaries, headers,
profile identity, commit identity, toolchain provenance, target mapping,
compile database / source facts, digests — `build-output.json` plus the
directory tree it describes. This is what makes "build once, scan many"
(S3) possible without abicheck ever running the build itself. See the
[Build Output Schema](../reference/build-output-schema.md) for the exact
contract and its validator's failure taxonomy.

## Source evidence

L3 (build)/L4 (source replay)/L5 (graph) evidence collected from a compile
database replay, the `abicheck-cc` compiler wrapper, or the Clang facts
plugin (`abicheck/buildsource/`) — either build-wide or target-specific. See
[Build Info & Sources](../learn/build-source-data.md) for the full model.
Every evidence pack must declare which target(s) it projects onto
(`evidence.projection: "declared"` in `build-output.json`) — a pack is never
automatically assumed to belong to every DSO in a build, the S16 boundary
[ADR-047 §9](../contribute/adr/047-github-actions-integration-model.md#9-source-evidence-safe-model-now-vs-full-model-later)
documents.

## Baseline channel

The named lifecycle source a check's baseline comes from:

- **`release-contract`** — immutable; published from a shipping-equivalent
  build whenever a release is cut. Never substitutes for asking "did this PR
  break what main already accepted."
- **`accepted-main`** — mutable; refreshed on every default-branch push.
  Answers "did this PR introduce a break vs. what main already accepted,"
  never a release promise.
- **`explicit`** — a specific tag/version, or a baseline file committed
  directly into the repository (S1's minimal case).
- **`none`** — no baseline at all; a single-build audit (S5), advisory by
  default.
- a project-defined custom channel.

See [`publish-baseline`/`update-main-baseline` Reference](../reference/publish-baseline.md)
for how `release-contract`/`accepted-main` are produced, and
[`resolve-baseline` Action Reference](../reference/resolve-baseline.md) for
how a check resolves one.

## Baseline set

One atomic manifest (`manifest.json`) plus one snapshot per target — and, for
a bundle-scoped baseline, a `binaries/` directory of each member's real ELF
binary — for one build profile or release bundle. What `actions/baseline`
already produces; see
[`resolve-baseline` Action Reference](../reference/resolve-baseline.md) for
the manifest schema and resolution rules.

## Check

One application of policy to `target × profile × baseline channel ×
evidence requirement`. This is the unit of accounting the whole model is
built around — a CI run performs one or more checks, each with its own
identity (`check_id`: `target@profile#baseline_channel@requested_depth`) and
its own report, never implicitly folded into one shared result. See the
[`check-target` Action Reference](../reference/check-target.md) for the
report envelope every check produces.

## Run plan

The exact, immutable description of which checks a CI run performs — derived
from `.abicheck.yml`'s `targets:`/`bundles:`/`profiles:`/`checks:` plus each
contract profile's `build-output.json`. Previously implicit in workflow YAML
+ matrix configuration; now a machine-readable artifact (`run-plan.json`) a
run can audit and project down to `abicheck aggregate`'s manifest shape.
`generate_run_plan()` has no built-in changed-path/changed-component filter
— every declared check is always included in the generated plan; scoping a
run to only the components a diff touched (e.g. in a monorepo, S25) is
something the caller does externally, by conditionally skipping matrix
cells or `check-project.yml` calls before this plan is generated, not
something `project plan` does for you. See the
[Run Plan Schema](../reference/run-plan-schema.md).

## Report

The result of one check: verdict, severity/gate decision, and full identity
(target, profile, candidate, baseline, config, commit, evidence depth). The
existing `compare`/`scan` JSON report, extended with the identity fields
[ADR-047 §7](../contribute/adr/047-github-actions-integration-model.md#7-report-envelope)
requires (`check_id`, `compatibility_verdict`, `policy_gate_decision`,
`check_evidence_coverage`, ...) — additive, so an existing consumer of the
plain `verdict`/`severity` fields keeps working unchanged.

## Fan-in

Combining multiple reports into one CI status — `abicheck aggregate`
(S28). Explicitly scoped to *this one* scenario, not the center of the
architecture: most scenarios (a single library, a single build profile) never
need a fan-in step at all, since a single check's own report is already the
whole answer.

## See also

- [Project Integration](index.md) — the scenario-first "which page do I need"
  index this glossary supports.
- [ADR-047 §1](../contribute/adr/047-github-actions-integration-model.md#1-domain-model) —
  the source domain-model table, including the rationale for why these seven
  boundaries matter and are easy to conflate.
