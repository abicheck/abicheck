---
doc_type: hub
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - integration-scenario-selection
lifecycle: active
generated: false
---

# Project Integration: Which Scenario Am I?

This section answers a different question than
[Choose Your Workflow](../start/choose-your-workflow.md). That page picks
a **CLI command** for one artifact shape you already have on disk right now.
This page picks a **project integration lifecycle** — how your repository's
CI wires together building, publishing baselines, and gating pull requests
over time, for however many libraries/consumers/plugin contracts your project
ships and however many build profiles it supports.

The underlying model is [ADR-047](../contribute/adr/047-github-actions-integration-model.md):
a project's ABI/API surface is checked as one or more **checks** — each an
application of policy to `target × profile × baseline channel × evidence
requirement` — not as one implicit `aggregate` report. See
[Concepts](concepts.md) for the full vocabulary (target, profile, baseline
channel, check, run plan, ...).

> Not every scenario below needs a dedicated `scenarios/*.md` page — where an
> existing reference or concept page already answers the question directly,
> this index links there instead of duplicating it into a thin wrapper page.
> The scenario ID (`S`-number) is the stable cross-reference either way: it
> names a row in
> [ADR-047 §8](../contribute/adr/047-github-actions-integration-model.md#8-condensed-scenario-catalog-s1s28)'s
> full catalog, independent of which page currently hosts the answer.

## Which Actions building block do I use?

Every scenario below eventually points at one of three composition layers.
They are not a "recommended vs. legacy" ladder — each is the right choice
for a different composition shape. The dependency graph isn't a clean
two-tier "both upper layers built from the primitives below," though:
reusable workflows do call `check-target`, but `check-target` itself
internally invokes the root `abicheck/abicheck` Action (alongside
`resolve-baseline`/`collect-facts` as sibling steps) to run the actual
scan/compare — so the root Action sits *underneath* `check-target` in the
real call graph, the reverse of a simple bottom-to-top ladder. The practical
answer to "which do I use" doesn't depend on that internal wiring, only on
your own orchestration shape:

| Layer | What it is | Reach for it when | Reference |
|---|---|---|---|
| **Single-Action step** | The root `abicheck/abicheck` composite Action — one step you nest inside a job you already have (your own build job, your own checkout). | You have one target to check and want to add it to an existing job with minimal new CI structure — this is the [Quick start](../use/github-action.md#quick-start) path. | [GitHub Action](../use/github-action.md) |
| **Reusable workflow** | `check-single.yml` / `check-project.yml` — a `workflow_call` job that always runs on its own fresh runner, with its own artifact-staging contract. Internally calls `check-target`. | You have several targets/profiles/baseline channels to fan out over (`check-project.yml`), or you want one isolated check job without hand-composing the primitives yourself (`check-single.yml`). | [Reusable Workflows](../reference/reusable-workflows.md) |
| **Primitive Actions** | `actions/check-target`, `actions/resolve-baseline`, `actions/baseline`, `actions/collect-facts` — the composable building blocks the reusable workflows are assembled from. `check-target` itself composes `resolve-baseline`/`collect-facts` plus the root single-Action step above for its actual analysis. | Neither layer above fits your orchestration as-is — e.g. you need `resolve-baseline`'s typed outcome without `check-target`'s report envelope, or a custom job topology around one primitive. | [`check-target`](../reference/check-target.md), [`resolve-baseline`](../reference/resolve-baseline.md), [`publish-baseline`/`update-main-baseline`](../reference/publish-baseline.md) |

`check-project.yml`'s cross-repository artifact-staging convention is newer
and less battle-tested than the single-Action step — see the status note on
[Reusable Workflows](../reference/reusable-workflows.md) before committing a
large migration to it.

## Getting started

| Scenario | Question | Where to look |
|---|---|---|
| S1 | One library, baseline is a file committed in the repo | [Scenario: Single Library](scenarios/single-library.md) |
| S2 | One library, baseline is "the last release" | [GitHub Action](../use/github-action.md), [Baseline Management](../use/baseline-management.md) |
| S5 | I just want an audit — no baseline to compare against yet | [Scenario: Single-Build Audit](scenarios/single-build-audit.md) |
| S6 | I have public headers, not just a binary | [Scenario: Header-Aware Compatibility](scenarios/header-aware-check.md) |

## Reusing an existing build

| Scenario | Question | Where to look |
|---|---|---|
| S3 | My build is expensive — build once, check many times | [Scenario: Reuse an Existing Build](scenarios/existing-build-artifact.md) |
| S4 | I want to build and check in the same CI job | [`check-target` Action Reference](../reference/check-target.md) |
| S13 | I only have prebuilt packages, no source checkout | [Scenario: Packages & Prebuilt Artifacts](scenarios/packages-and-sdks.md) |
| S18 | I cross-compile; the check can't run on the build host | [Scenario: Cross Compilation](scenarios/cross-compilation.md) |
| S12 | My build is Bazel/sandboxed | [Build Output Schema](../reference/build-output-schema.md) |

## Deeper source/build evidence

| Scenario | Question | Where to look |
|---|---|---|
| S7 | I want PR-scoped source-level checks (macros, inline, templates) | [Scenario: Source Scan via Compile-DB Replay](scenarios/source-replay.md) |
| S8, S9 | My build can emit source facts as it compiles | [Scenario: Source Facts From the Build Itself](scenarios/build-integrated-facts.md) |
| S10 | Some of my public headers are generated by codegen | [Build Output Schema](../reference/build-output-schema.md) |
| S11 | My build is Make-based / doesn't emit a compile database | [Scenario: Source Facts From the Build Itself](scenarios/build-integrated-facts.md) (the wrapper needs no compile database) |
| S16 | I have one shared facts pack for several DSOs | [Build Info & Sources](../learn/build-source-data.md), [Build Output Schema](../reference/build-output-schema.md) |

## Multiple libraries, profiles, or channels

| Scenario | Question | Where to look |
|---|---|---|
| S14 | My libraries ship together and depend on each other (a release bundle) | [Scenario: Multi-DSO Release Bundle](scenarios/release-bundle.md) |
| S15 | I have several independent libraries built together | [Scenario: Multiple Independent Targets](scenarios/multi-dso-project.md) |
| S17 | I need to check the same library across several build profiles | [Scenario: Multiple Build Profiles](scenarios/multi-platform.md) |
| S21 | I want to gate on two different baselines at once (e.g. last release *and* main) | [`check-target` Action Reference](../reference/check-target.md) |
| S25 | I have a monorepo with several independently-versioned components | [Scenario: Monorepo / Multiple Components](scenarios/monorepo.md) |
| S28 | I need one CI status from several checks | [Reusable Workflows](../reference/reusable-workflows.md) (`check-project.yml`'s trailing `aggregate` job) |

## Baselines

| Scenario | Question | Where to look |
|---|---|---|
| S19 | Publish an immutable baseline whenever we cut a release | [`publish-baseline`/`update-main-baseline` Reference](../reference/publish-baseline.md) |
| S20 | Keep a rolling "what did `main` already accept" baseline | [`publish-baseline`/`update-main-baseline` Reference](../reference/publish-baseline.md) |

## Beyond a plain library ABI

| Scenario | Question | Where to look |
|---|---|---|
| S22 | Will this library change break a specific application that links it? | [Scenario: Application & Plugin Contracts](scenarios/application-and-plugin-contracts.md) |
| S23 | I have a plugin/`dlopen`/`dlsym` contract, not a public-header ABI | [Scenario: Application & Plugin Contracts](scenarios/application-and-plugin-contracts.md) |
| S24 | Will this binary resolve its dependencies in a given rootfs/container? | [Scenario: Dependency & Container Checks](scenarios/dependency-and-container-checks.md) |

## Rollout and process

| Scenario | Question | Where to look |
|---|---|---|
| S26 | I'm migrating from another ABI tool and want a shadow/advisory rollout first | [Scenario: Migration & Rollout](scenarios/migration-and-rollout.md) |
| S27 | This PR contains an intentional breaking change | [Scenario: Migration & Rollout](scenarios/migration-and-rollout.md) |

## See also

- [Concepts](concepts.md) — the domain-model glossary (target, profile,
  baseline channel, check, run plan, ...) every scenario above is expressed in.
- [ADR-047](../contribute/adr/047-github-actions-integration-model.md) — the
  full design rationale and decision log behind this model.
- [Choose Your Workflow](../start/choose-your-workflow.md) — the
  CLI-command-level decision guide, for when you already know what you're
  comparing and just need the right flags.
