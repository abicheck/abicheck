---
doc_type: contributor
level: advanced
lifecycle: active
---

# Module boundaries and file-health convergence

**Status:** Proposed architecture and staged migration plan. Phase 0 is the
machine-readable boundary contract, the delta-based no-growth gate, and scoped
agent guidance added with this document.

**Assessed tree:** `main@793849f3cb7c3eb7c5add2b90ca31b6c5849834b`
(2026-08-24, after PR #848 merged).

**Related work:** [Duplication and convergence assessment](duplication-and-convergence-assessment.md),
[CLI cleanup, phase two](cli-cleanup-phase-two.md),
[ADR-037](../adr/037-cli-interface-contract.md),
[ADR-055](../adr/055-typed-request-result-completeness-and-schema-registry.md),
and [ADR-056](../adr/056-multi-artifact-library-set-scan.md).

## Decision in one paragraph

The 2,000-line limit remains a final safety net, but it is no longer the design
mechanism. New production modules have an 800-line hard ceiling and a 500-line
review-pressure warning. Existing production modules above 800 lines are
**grandfathered but may not grow**. New top-level `cli_*`, `service_*`,
`reporter_*`, `dumper_*`, `diff_*`, `bundle_*`, and similar overflow siblings
are frozen. New and migrated code goes into explicit packages whose names state
the responsibility: `domain`, `evidence`, `compare`, `evaluate`, `storage`,
`workflows`, `report`, and `interfaces`. Dependency direction is checked. A
split is accepted only when it moves a responsibility and reduces coupling;
moving arbitrary functions to make a counter green is not an architectural
change.

## Why the present rule fails

The repository does not merely contain a few naturally large files. Current
`main` has at least these unrelated production modules ending exactly at line
2,000:

| Module | What it currently mixes |
|---|---|
| `abicheck/aggregate.py` | report loading, validation, coverage folding, gate folding, schema projection, matrix construction, and text rendering |
| `abicheck/bundle.py` | bundle models re-export, ELF loading, graph construction, detectors, policy-aware verdict work, and compatibility surface |
| `abicheck/buildsource/inline.py` | project configuration schema/model, precedence-facing values, build querying, L3/L4/L5 extraction, and coverage construction |
| `abicheck/buildsource/source_graph.py` | graph model/building plus compatibility re-exports into extracted sibling modules |
| `abicheck/change_registry.py` | a large declarative catalog plus extension aggregation |
| `abicheck/dumper_castxml.py` | CastXML traversal and many entity-specific parsers |
| `abicheck/reporter.py` | report construction, JSON projection, severity/gate projection, and a large compatibility re-export surface |
| `abicheck/reporter_markdown.py` | Markdown grouping, rendering, root-cause projection, and stat/review variants |

Several more are within a few lines of the same cap: `cli_compare_helpers.py`
and `cli_compare_release.py` at 1,998, `cli_options.py` at 1,977, and `cli.py`
at 1,959. PR #848 explicitly deferred user-facing CLI/config wiring because
all plausible dispatch files had no line budget left. That PR has now merged
and introduced `abicheck/bundle_analysis.py`: a reasonable 201-line
orchestrator in isolation, but also one more top-level `bundle_*` sibling
because no bounded bundle/release package exists yet. The issue is therefore
both product back-pressure at the cap and continued flat-namespace growth
below it.

The most direct repository evidence is
`changelog.d/20260824_030000_claude_fix_2000line_hardcap_violations.md`: it
calls two extractions “purely file-size-cap splits,” preserves old import and
patch sites through re-exports, and extends an import-cycle allowlist. That is
a truthful description of the change, but also the failure mode this plan
stops: the physical file changed while ownership, dependency direction, and
public/internal surface did not.

The current AI-readiness rule reinforces this behavior:

- one warning threshold (1,500) and one hard threshold (2,000);
- an allowlist for files beyond the hard threshold;
- generic advice to split into helpers or `_lib/`;
- no check for dependency direction, responsibility, façade size, or
  top-level pseudo-package growth;
- the checker itself is above 2,400 lines and allowlisted.

A line count is useful, but only as a lagging pressure metric. It cannot answer
whether a module has one reason to change.

## Root causes

### 1. A flat namespace substitutes prefixes for packages

The package is dominated by families such as `cli_*`, `service_*`, `diff_*`,
`dumper_*`, `reporter_*`, `bundle_*`, and `contract_*`. Prefixes make search
possible, but they do not define import direction or ownership. When a host
fills up, the easiest local action is another sibling with the same prefix.
The result is a pseudo-package with no package boundary and no place for a
small scoped `AGENTS.md`.

### 2. Internal implementation names behave like public APIs

Many extractions retain re-exports because tests monkeypatch the historical
module, sibling modules import private helpers from it, or an internal patch
site has become convention. This makes moving code expensive and keeps the
old module responsible for the new module forever. Public compatibility is
important; compatibility for every underscore-prefixed test hook is not.

### 3. Frontends, workflows, and domain behavior are interleaved

`cli.py` contains root Click behavior, helper implementations, exit semantics,
provenance work, and compatibility re-exports. `buildsource/inline.py` owns
both project config and extraction. `reporter.py` imports a large Markdown
surface back under its historical names. These combinations produce cycles
and make every feature search touch several large files.

### 4. Different kinds of large files receive the same remedy

A declarative change-kind catalog, a backend parser, a workflow orchestrator,
and a CLI adapter do not have the same decomposition boundary:

- a catalog should partition by stable taxonomy while preserving one registry;
- a parser should partition by parsed entity with a shared context;
- an orchestrator should partition by resolve/execute/project phases;
- an interface should be thin and delegate to typed workflows.

“Move some helpers” is not valid guidance for all four.

### 5. Agent guidance describes danger, not navigation

`abicheck/CLAUDE.md` currently tells an agent that several files are “large and
intentionally so.” It does not give a deterministic answer to “where does a
new extractor, detector, policy rule, workflow, or CLI option belong?” An
agent under task pressure therefore follows the nearest spelling and available
line room.

## Target architecture

The target is a package dependency graph, not an immediate big-bang rename:

```text
interfaces ───────► workflows ───────► evidence
     │                  │             compare
     │                  │             evaluate
     │                  │             storage
     │                  └────────────► domain
     └────────────► report ──────────► domain / compare / evaluate

storage ─────────► domain
evidence ────────► domain
compare ─────────► domain
evaluate ────────► domain / compare
```

The machine-readable source of truth is
`architecture/module-boundaries.json`.

### `abicheck/domain/`

Stable facts and value objects: snapshots, entities, finding identity,
comparability facts, result/decision records, and the change-kind catalog.
No filesystem access, subprocesses, Click, formatting, or workflow dispatch.

### `abicheck/evidence/`

L0-L4 fact extraction and normalization: binary formats, debug formats,
CastXML/direct-Clang header AST, build-system evidence, and source replay.
Extractors report facts, coverage, provenance, and degradation. They never
choose severity or format a user report.

### `abicheck/compare/`

Old/new matching, diff detectors, comparability, type/source reachability, and
bundle-level cross-artifact analysis. It emits typed findings and supporting
evidence. It does not apply a user gate policy.

### `abicheck/evaluate/`

Suppression, contract relevance, policy classification, severity, analysis
assurance, and exit/gate decisions. It consumes findings; it does not parse
binaries or mutate rendered output.

### `abicheck/storage/`

Versioned serialization, schemas, snapshot/report envelopes, caches, and
baseline-file I/O. It persists typed objects without reinterpreting verdicts.

### `abicheck/workflows/`

Typed orchestration for `dump`, `compare`, `scan`, `deps`, `aggregate`,
`project`, and bundle/release operations. A workflow follows the convergence
plan’s established shape:

```text
request -> resolve plan -> execute -> typed result
```

It owns resource lifetime and stage ordering. It does not contain Click option
handling or output-format branches.

### `abicheck/report/`

Pure projections from immutable typed results to a canonical report model and
then JSON, Markdown, HTML, SARIF, JUnit, stat, or review-digest formats.
Rendering cannot change classification or gate state.

### `abicheck/interfaces/`

Click commands, the typed Python façade, and compatibility adapters. An
interface validates syntax, builds a typed request, invokes a workflow, and
selects a report projection. It does not implement extraction, comparison, or
policy rules.

## Compatibility during migration

The project is pre-1.0, but existing supported Python imports still need a
controlled transition. Compatibility follows three rules:

1. **Only documented public imports receive a façade.** A façade has a module
   docstring, explicit `__all__`, no domain logic, and a target maximum of 150
   lines.
2. **Private monkeypatch/import sites migrate with the implementation.** Tests
   patch the owner of the behavior, not the historical caller module.
3. **Every façade has an exit condition.** Record the public symbols it
   preserves and the release or phase that can remove it. A re-export with no
   consumer inventory is permanent debt, not compatibility.

No new import-cycle or boundary allowlist entry is an acceptable consequence
of a size split. If a move creates a cycle, the responsibility boundary is
wrong or a shared value object belongs lower in the graph.

## File-health policy

The new gate is deliberately asymmetric so cleanup can land incrementally:

| File class | Review warning | New-file maximum | Existing debt rule |
|---|---:|---:|---|
| `abicheck/**/*.py` | 500 | 800 | files already above 800 may not grow |
| `scripts/**/*.py` | 600 | 1,000 | files already above 1,000 may not grow |
| `tests/**/*.py` | 900 | 1,200 | files already above 1,200 may not grow |

These are physical-line pressure thresholds, not quality scores. A 300-line
module can still be badly designed; a 900-line generated catalog can be valid.
Exceptions therefore require a typed reason, not a generic allowlist:
`generated`, `declarative_catalog`, or `backend_parser`, plus an owner and a
review date. Phase 0 intentionally adds no new exception mechanism; the current
repository needs shrink pressure before it needs more ways around it.

The old 2,000-line hard stop can remain during migration. Once every legacy
file is below it, remove duplicate size enforcement from
`check_ai_readiness.py` and let one focused check own the policy.

## What counts as a valid split

A refactor PR that claims to reduce a large module must satisfy all of these:

- names a responsibility being moved, not a line range;
- gives the destination package a one-sentence contract;
- leaves one authoritative implementation;
- does not add an import-cycle/boundary allowlist entry;
- does not preserve private re-exports solely for old test patch sites;
- reduces the source module by at least 20% or below 800 lines, unless it is a
  preparatory dependency inversion with a named next PR;
- adds tests at the new public/workflow boundary, not only tests of moved
  private helpers;
- records before/after line count, imports, and strongly connected component
  membership in the PR body;
- updates the nearest scoped `AGENTS.md` task-routing table.

A new sibling containing one displaced function while the old module re-exports
it and still owns its callers does not meet this definition.

## Migration sequence

### Phase 0 — stop creating new debt (this plan)

- Commit `architecture/module-boundaries.json`.
- Add `scripts/module_architecture.py` and focused tests.
- Run it on pull requests with the base SHA so grandfathered large files may
  shrink but not grow.
- Freeze new top-level overflow-prefix modules.
- Add `abicheck/AGENTS.md` with task-first routing.
- Replace package-local vendor guidance with a pointer to the scoped,
  vendor-neutral contract.

This phase changes no runtime behavior and moves no production implementation.
That is intentional: another unreviewed mechanical move would repeat the
problem being addressed.

### Phase 1 — prove the pattern with `aggregate`

`aggregate.py` is the best pilot because its responsibilities are visible and
its public surface is narrower than CLI/extraction:

```text
abicheck/workflows/aggregate/
  request.py       expected-target and input request types
  load.py          report reading and strict validation
  fold.py          compatibility/gate/coverage/contract folds
  findings.py      finding-matrix reconciliation
  result.py        immutable aggregate result model
  render.py        text and JSON projection adapters
```

`abicheck/aggregate.py` becomes a temporary public façade only for documented
imports; `cli_aggregate.py` calls the workflow package directly. Acceptance:
old/new JSON golden parity, all malformed-input tests preserved, no reverse
import to CLI, old module below 150 lines.

### Phase 2 — separate configuration, interfaces, and workflows

1. Move `BuildConfig` schema/model and config loading out of
   `buildsource/inline.py`; extraction receives an already-resolved config.
2. Introduce `workflows/dump`, `workflows/compare`, and `workflows/scan` around
   the existing typed request/result pipelines rather than creating new
   implementations.
3. Move command registration/options into `interfaces/cli/<command>.py`.
4. Reduce `cli.py` to root setup, command registration, and public façade.
5. Remove service-to-CLI imports one allowlisted edge at a time; never replace
   them with a new allowlist.

### Phase 3 — reporting and bundle/release

- Build one canonical report model before format rendering.
- Split `reporter.py` into model construction and JSON projection; split
  Markdown by semantic section, not arbitrary helper count.
- Split bundle facts/models and bundle detectors into `compare/bundle`.
- Put directory discovery, per-library execution, and release matching in
  `workflows/release`.
- Keep policy classification in `evaluate`, not bundle or report code.

### Phase 4 — evidence backends and declarative catalogs

- Partition CastXML/direct-Clang parsers by entity (`functions`, `records`,
  `enums`, `templates`, provenance) behind one backend context.
- Partition source-graph model/build/diff responsibilities.
- Partition the change catalog by stable taxonomy while constructing exactly
  one registry and validating globally unique IDs at import/test time.
- Move platform parsers only when the dependency graph and shared fact model
  are stable; parser churn before that would create more adapters.

### Phase 5 — remove migration scaffolding

- Delete obsolete top-level private re-exports.
- Remove resolved cycle/boundary allowlist entries.
- Remove the old 2,000-line implementation from `check_ai_readiness.py` after
  the focused gate is the sole owner.
- Collapse empty compatibility modules.
- Update `pyproject.toml` mypy overrides from flat module names to package
  patterns.

## Prioritized module inventory

| Priority | Current area | First architectural move |
|---:|---|---|
| P0 | `aggregate.py` | pilot workflow package; separate load/fold/result/render |
| P0 | `buildsource/inline.py` | split config model/resolution from extraction execution |
| P0 | `cli.py`, `cli_options.py`, compare/release helpers | thin interfaces over typed workflows |
| P0 | `reporter.py`, `reporter_markdown.py` | canonical model, then pure format renderers |
| P0 | `scripts/check_ai_readiness.py` | check registry plus independent focused gates; no self-exemption |
| P1 | `bundle.py` and release CLI | bundle analysis vs release orchestration |
| P1 | `buildsource/source_graph.py` | graph values, construction, comparison, findings |
| P1 | `change_registry.py` | taxonomy catalogs feeding one validated registry |
| P2 | `dumper_castxml.py`, `dumper_clang.py` | entity parsers sharing one backend context |
| P2 | remaining large platform/storage modules | move only after lower-layer models stabilize |

## AI-agent operating model

An agent should need two documents before editing production code: root
`AGENTS.md`, then the nearest scoped `AGENTS.md`. The package-level guide uses
questions rather than a 150-file inventory:

- collecting a new fact -> `evidence`;
- detecting old/new change -> `compare`;
- deciding relevance/severity/gate -> `evaluate`;
- orchestrating an operation -> `workflows`;
- formatting a result -> `report`;
- adding a flag/API adapter -> `interfaces`;
- changing persisted shape -> `storage`;
- changing shared values/identity -> `domain`.

Each target package receives a short `AGENTS.md` when its first real code lands:
purpose, allowed imports, canonical entry points, test location, and three to
five “do not” rules. Do not pre-create empty package trees or duplicate the
root manual.

## Acceptance metrics

The initiative is complete when all of the following are true:

1. no production module ends at or exceeds 2,000 lines;
2. no product PR is deferred because a host file has no line budget;
3. no new top-level overflow-prefix module has landed since Phase 0;
4. every production file above 800 is either shrinking in an active phase or
   carries a typed, reviewed parser/catalog exception;
5. target-package import checks are clean with no cycle allowlist;
6. `cli.py` and supported compatibility façades are at most 150 lines each;
7. report formats consume one canonical model and do not recompute gate state;
8. extraction code imports neither Click nor policy/report modules;
9. an agent can route a representative task to the owning package using the
   package `AGENTS.md` without opening a 2,000-line module first;
10. the existing duplication/convergence plan’s shared request/result pipelines
    remain the implementation, rather than being re-created under new names.

## Risks and controls

**Import compatibility:** preserve only inventoried public imports and test them
explicitly. Do not infer public status from widespread private test imports.

**Merge conflicts:** one vertical responsibility per PR; avoid directory-wide
renames. Land dependency inversion before moving callers when necessary.

**Performance regressions:** moving code must not add serialization or copy
large snapshots between layers. Typed results are passed in process.

**Architecture astronautics:** packages are created only when real code moves.
No empty hierarchy, dependency-injection framework, plugin system, or generic
repository abstraction is introduced by this plan.

**Two competing architectures:** this plan owns physical module boundaries and
agent navigation. The duplication/convergence plan owns semantic workflow
unification. Its `ArtifactRequest -> ResolvedArtifactPlan -> ArtifactResult`
shape remains authoritative inside the new `workflows` package.
