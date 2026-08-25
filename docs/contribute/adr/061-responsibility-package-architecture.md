# ADR-061: Responsibility-Package Architecture and Flat-Namespace Migration

**Date:** 2026-08-24
**Status:** Accepted — Phases 0-1 implemented; Phase 2 in progress; Phases 3-5 remain incremental.
**Decision maker:** abicheck maintainers

## Context

`abicheck` has outgrown its predominantly flat package layout. Large modules
such as `aggregate.py`, `analysis_assurance.py`, and `appcompat.py` coexist
with expanding prefix families such as `aggregate_*`, `cli_*`, `service_*`,
`diff_*`, and `reporter_*`. Those prefixes provide visual proximity, but they
do not establish ownership, a supported entry point, or a permitted dependency
direction. They are packages in naming convention only.

The visible symptom is a collection of files near the repository's historical
2,000-line ceiling. The architectural problem is broader:

1. **Physical ownership is ambiguous.** A contributor adding compare behavior
   can plausibly choose `cli.py`, `cli_options.py`, one of several
   `cli_compare_*` files, `service.py`, or a `service_*` pipeline. Nothing in
   the filesystem answers which module owns the behavior.
2. **Mechanical splitting preserves coupling.** Moving functions into a new
   sibling while re-exporting them from the old module reduces line count but
   leaves callers, monkeypatch targets, and reverse imports attached to the
   original owner.
3. **Architectural roles are mixed.** Frontend translation, orchestration,
   extraction, comparison, policy evaluation, exit-code selection, and
   rendering can occur in one call chain without typed stage boundaries.
4. **Incidental paths acquire compatibility weight.** Internal imports and
   private monkeypatch locations are often treated as if they were documented
   APIs. This prevents ownership transfer and makes every split permanent.
5. **Repository guidance compensates for the layout.** Agent instructions
   have accumulated a detailed module inventory, implementation history,
   dynamic counts, and case-specific investigations because the package tree
   itself does not communicate where new work belongs.

The newer typed compare and dump paths demonstrate the better shape already:
a typed request is resolved explicitly, executed, classified, and returned as
a typed result. This ADR standardizes that shape across the repository and
gives it a physical package model.

This is a repository architecture decision, not a mass-rename proposal. The
target tree describes where code belongs after incremental migrations. Empty
packages are not created merely to resemble the diagram.

## Decision drivers

- A contributor or agent must be able to route a change without first reading
  a multi-thousand-line module or a historical instruction manual.
- Facts, compatibility findings, policy decisions, workflow results, and
  rendered output must each have one owner.
- Dependency direction must be machine-checkable and must not rely on a
  growing cycle allowlist.
- Existing documented imports and command behavior must remain compatible
  while implementation ownership moves.
- Migration must proceed as behavior-preserving vertical slices rather than a
  repository-wide flag day.
- Line-count enforcement must prevent new debt without confusing file size
  with architectural quality.
- Dry-run and normal execution must resolve configuration through the same
  path.

## Decision

Adopt eight responsibility packages, arranged in three conceptual rings, and
freeze the flat `abicheck/` namespace against new implementation families.

```text
                            frontends
                           /         \
                    workflows       report
                    /   |   |  \       |
              extract compare policy storage
                    \   |   |  /
                         model
```

Imports point inward/downward. A reverse import is an architecture defect,
not a reason to extend an exception list.

The end-to-end data flow is:

```text
CLI / Python API
    -> typed Request
    -> resolved Plan
    -> Extraction Result / Snapshot
    -> Raw Findings
    -> Policy Decision
    -> Workflow Result
    -> ReportDocument
    -> JSON / Markdown / HTML / SARIF / JUnit
```

Each fact and decision is computed once. Later stages project or render it;
they do not reconstruct it.

### D1. Responsibility packages and dependency contracts

| Package | Owns | May depend on | Must not own |
|---|---|---|---|
| `model` | Immutable shared domain values and persisted/public identities | Standard library and lightweight typing dependencies | Filesystem access, subprocesses, Click, rendering, policy execution |
| `storage` | Snapshot/baseline serialization, cache behavior, snapshot/baseline schemas, migrations | `model` | Extraction, compatibility decisions, report schemas, presentation |
| `extract` | Reading binary, debug, header, build, and source evidence into facts | `model`, `storage` | Severity, suppression, gate decisions, user-facing output |
| `compare` | Comparability, old/new matching, identity, detectors, raw findings | `model` | User policy, suppression, exit codes, rendering |
| `policy` | Effective configuration, contract relevance, suppression, classification, assurance, severity, gate decisions | `model`, `compare` | Parsing artifacts, running compilers, rendering reports |
| `workflows` | Operation orchestration, sequencing, resource lifetime, and request/plan/result composition | `model`, `storage`, `extract`, `compare`, `policy` | Click concepts and format rendering |
| `report` | The canonical immutable `ReportDocument`, report schemas, and pure format projections | `model`, `compare`, `policy`, `workflows` | Re-running comparison or changing findings, severity, verdicts, or gate state |
| `frontends` | CLI, typed-Python, and compatibility input translation and output selection | `model`, `workflows`, `report` | Extraction algorithms, precedence rules, and business decisions |

The dependency list is exact for first-party responsibility packages. A
package may use third-party libraries appropriate to its role, but a
third-party import must not be used to bypass an architectural boundary.

`errors.py`, documented compatibility type modules, and root entry points may
remain at package root as explicitly classified public surfaces. They are not
an unbounded ninth layer.

### D2. End-state physical layout

The following is a destination map. A directory is created only when at least
one implementation and its tests move into it.

```text
abicheck/
  __init__.py                 documented public exports only
  __main__.py                 frontend entry point
  cli.py                      temporary/public facade
  service.py                  typed-Python facade
  api_types.py                compatibility exports during migration
  errors.py                   supported public exceptions

  model/
    entities.py
    snapshot.py
    findings.py
    coverage.py
    decision.py
    change_catalog/
      registry.py
      symbols.py
      types.py
      platform.py
      build.py
      source.py

  storage/
    snapshot.py
    baseline.py
    cache.py
    schema.py                   snapshot/baseline schema ownership
    migrations.py

  extract/
    protocols.py
    binary/{elf,pe,macho}.py
    debug/{dwarf,pdb}/
    debug/{btf,ctf}.py
    headers/{castxml,clang}/
    build/{compile_commands,cmake,bazel,make}.py
    source/{graph,replay,provenance}.py

  compare/
    engine.py
    comparability.py
    identity.py
    filtering.py
    matching/{symbols,types,source_entities}.py
    detectors/{symbols,types,cpp,platform,build,source}.py
    bundle/{graph,matching,detectors}.py

  policy/
    effective_config.py
    contract.py
    suppression.py
    classification.py
    assurance.py
    severity.py
    gate.py
    packs/

  workflows/
    artifact/{contracts,resolve,execute}.py
    dump/{contracts,resolve,execute}.py
    compare/{contracts,resolve,execute}.py
    scan/{contracts,resolve,execute}.py
    aggregate/{contracts,resolve,load,fold,reconcile,execute}.py
    release/{discovery,matching,execute}.py
    project.py
    dependencies.py
    appcompat.py

  report/
    document.py
    build.py
    grouping.py
    schema.py                   report schema ownership
    render/{json,markdown,html,sarif,junit}.py

  frontends/
    python_api.py
    cli/root.py
    cli/commands/{dump,compare,scan,aggregate,release}.py
    cli/options/{evidence,compiler,policy,output}.py
    compat/abicc.py

  compat/                      retained public namespace; delegation only
```

Every top-level responsibility package has a scoped `AGENTS.md` when it is
created. That file states purpose, allowed first-party imports, canonical
entry points, test locations, and prohibited responsibilities.

### D3. Task routing is authoritative

The root contributor/agent contract must include this routing table near its
beginning:

| Change | Owner |
|---|---|
| Read a new binary, debug, header, build, or source fact | `extract/` |
| Add or change an ABI entity/value shared across stages | `model/` |
| Match old/new entities or identify a change | `compare/` |
| Decide relevance, suppression, classification, severity, or gate effect | `policy/` |
| Coordinate dump, compare, scan, release, aggregate, project, or dependency behavior | `workflows/` |
| Serialize snapshots/baselines, maintain their schemas or migrations, or manage caches | `storage/` |
| Add a report field, report schema, or output format | `report/` |
| Add a CLI flag, API adapter, or ABICC translation | `frontends/` |

A new production file is not created until its owner can be selected from
this table. If no row applies, the contributor must amend the architecture
decision or explain why the behavior is a public root surface; inventing a
new prefix family is not the fallback.

### D4. Production module formation

Each module must have one completion sentence, such as "parses ELF dynamic
symbols," "matches old and new function identities," or "renders a
`ReportDocument` as Markdown." Descriptions such as "common utilities,"
"additional CLI logic," and "helpers used by compare" indicate that the
module has no stable owner.

New generic names are prohibited unless the architecture check carries a
narrow, documented exception:

```text
helpers.py  utils.py  common.py  misc.py  base.py  extra.py  more.py
*_helpers.py  *_utils.py  *_lib.py
```

Names describe the responsibility instead: `name_normalization.py`,
`compiler_flags.py`, `public_surface.py`, `report_grouping.py`,
`symbol_matching.py`, or `resource_lifetime.py`.

No new root implementation sibling may extend a pseudo-package family, for
example `cli_new_helpers.py`, `reporter_extra.py`, `service_scan_more.py`,
`diff_types_additional.py`, or `bundle_analysis_v2.py`.

Module docstrings normally occupy 5–20 lines and state:

- what the module owns;
- what adjacent concern it does not own; and
- its canonical entry point, when one exists.

PR chronology, incident narratives, dynamic counts, temporary migration
status, and individual known gaps do not belong in production docstrings.
Durable rationale belongs in an ADR; active defects belong in issues or a
small known-gap registry; user-visible changes belong in changelog material.

### D5. Size is a pressure signal with a hard new-code ceiling

| Module type | Normal target | Review warning | Hard maximum for a new file |
|---|---:|---:|---:|
| Normal production module | 100–400 | 500 | 800 |
| Compatibility facade | 20–100 | 120 | 150 |
| Package `AGENTS.md` | 40–100 | 120 | 150 |
| Root `AGENTS.md` | 150–250 | 300 | 350 |
| Test module | 100–600 | 800 | 1,200 |
| Parser or declarative catalog exception | 300–800 | 900 | 1,200 |

An exception above 800 lines is limited to generated code, a data-only
declarative catalog, or a parser whose state machine remains one
responsibility. It requires a debt record containing an owner, rationale,
recorded line baseline, target, and review date. It may not silently grow.

Existing oversized files are governed by no-growth baselines rather than an
immediate rewrite. The existing 2,000-line gate remains temporarily as a
backstop until the debt ledger covers all legacy exceptions and the focused
architecture check has demonstrated equivalent or stronger protection.

### D6. Imports expose ownership

Across responsibility packages, production code uses explicit absolute
imports from the canonical implementation module:

```python
from abicheck.model.findings import Finding
from abicheck.compare.engine import compare_snapshots
```

Within a package, relative imports are acceptable. New internal code must not
import behavior through `abicheck.service`, `abicheck.cli`, another legacy
facade, or a broad package re-export. A migrated implementation must never
import back through its old facade.

Import cycles are architecture defects. Permanent cycle allowlists and
`TYPE_CHECKING` imports used solely to hide a layering cycle are prohibited.
A temporary migration edge, if unavoidable, is recorded in `debt.yaml` with
an owner and expiry rather than added to the stable contract.

Package `__init__.py` files are small and inert. They may document and export
a narrow package surface through explicit `__all__`; they do not register
plugins, inspect the environment, touch files, load every submodule, or hold
product logic. Internal callers prefer the implementation module over a
package-wide re-export.

### D7. Major workflows use Request -> ResolvedPlan -> Result

Every major operation is divided into `contracts.py`, `resolve.py`, and
`execute.py` (with responsibility-specific modules alongside them where
needed).

`contracts.py` contains typed inputs and outputs only:

```python
@dataclass(frozen=True)
class ScanRequest:
    candidate: ArtifactInput
    baseline: BaselineInput
    configuration: ScanConfiguration

@dataclass(frozen=True)
class ResolvedScanPlan:
    candidate_plan: ResolvedArtifactPlan
    baseline_plan: ResolvedArtifactPlan
    effective_policy: EffectivePolicy

@dataclass(frozen=True)
class ScanResult:
    comparison: ComparisonResult
    decision: GateDecision
    coverage: CoverageSummary
    timings: StageTimings
```

- Requests express user intent without Click concepts.
- Plans contain normalized, fully resolved effective values and provenance.
- Results contain achieved facts and decisions, not formatted output.
- Values are immutable unless resource ownership requires a deliberately
  controlled context manager.
- Stages do not exchange large untyped dictionaries.

`resolve.py` turns a request into a plan. It owns precedence, validation,
normalized paths, backend/evidence selection, compiler/build configuration,
and resource preparation. It does not compare artifacts or render output.

`execute.py` consumes the plan. It owns stage ordering, resource lifetime,
extraction, comparison, policy evaluation, timings, and degradation
collection. A composition entry point has the conceptual form:

```python
def run_scan(request: ScanRequest) -> ScanResult:
    with resolve_scan_request(request) as plan:
        return execute_scan_plan(plan)
```

Dry-run renders that same resolved plan. A separate estimator may summarize
cost, but it may not independently predict effective backend, depth, policy,
or configuration.

### D8. Compatibility facades preserve public paths, not private coupling

Documented public modules such as `abicheck.service`, `abicheck.cli`, and
documented type modules may remain while implementation moves. A facade:

- stays below 150 lines;
- has explicit `__all__`;
- delegates or re-exports only;
- contains no domain logic;
- is used by external callers, not new internal code;
- documents whether its path is permanently supported or scheduled for
  removal.

A private re-export is not retained solely because a test monkeypatches it.
The test moves with the implementation and patches the actual owner. Facade
tests verify delegation and supported import compatibility; they do not
retest the underlying algorithm.

### D9. Catalogs, parsers, and renderers have specific shapes

**Catalogs.** The change registry is partitioned by taxonomy, not into one
file per change kind. Declarative modules such as `symbols.py`, `types.py`,
`platform.py`, `build.py`, and `source.py` feed one `registry.py`, which
validates globally unique identifiers, complete metadata, valid references,
and non-contradictory defaults. Detection remains in `compare`; policy
algorithms remain in `policy`.

**Parsers.** Large backend parsers are divided by parsed entity or parser
state responsibility, never arbitrary line ranges. A CastXML package, for
example, may contain `context.py`, `location.py`, `type_resolution.py`,
`functions.py`, `records.py`, `enums.py`, `templates.py`, and `backend.py`.
`backend.py` coordinates traversal; entity modules parse one class of node
using shared context. They do not independently open input, resolve global
configuration, or create policy findings.

**Renderers.** Every renderer is a pure projection:

```python
def render_markdown(document: ReportDocument) -> str:
    ...
```

A renderer cannot remove findings, change severity, reconstruct a verdict,
calculate an exit code, repair workflow omissions, or mutate its input. All
formats consume the same immutable `ReportDocument`, built once from the
workflow result.

### D10. Tests mirror responsibility ownership

The intended test topology is:

```text
tests/
  unit/{model,storage,extract,compare,policy,workflows,report,frontends}/
  contract/{public_api,cli,schemas,compatibility_imports}/
  integration/{extract,workflows,platforms}/
  golden/reports/
  factories/{snapshots,findings,artifacts}.py
  fixtures/
```

Existing tests migrate with their production implementation rather than in a
separate cosmetic reorganization. Unit tests patch the owner module. Test
names describe behavior rather than private function names. Golden tests pin
stable output contracts but do not replace semantic assertions. Shared test
construction uses responsibility names (`snapshot_factory.py`), not generic
`test_helpers.py`. Large tests split by scenario or contract axis, never by
line number.

### D11. Agent guidance is a routing contract, not a history database

The root `AGENTS.md` targets 200–300 lines (350 hard maximum) and contains:

1. project purpose and supported/development Python versions;
2. the task-to-package routing table;
3. dependency direction and public compatibility rules;
4. canonical verification commands;
5. a change checklist; and
6. links to architecture decisions, contributor documentation, and the
   current issue tracker.

It does not reproduce every module, current detector/test counts, bug
investigations, implementation chronology, temporary migration state, or
statements normalizing a large module as "intentionally" large.

Each responsibility package's scoped `AGENTS.md` is 60–120 lines and answers
only: purpose, permitted imports, canonical entry points, test locations, and
prohibited responsibilities. Tool-specific adapters such as `CLAUDE.md` and
Copilot instructions remain 5–20-line pointers to the canonical root and
nearest scoped instructions; they do not fork architecture policy.

Dynamic facts stay with generated repository facts. Durable design history
stays in ADRs. Active defects stay in issues or a small machine-readable
known-gap registry.

### D12. Stable architecture and temporary debt are separate data

Create these files during Phase 0:

```text
architecture/
  README.md
  modules.yaml
  debt.yaml
```

`modules.yaml` is the stable, desired dependency contract:

```yaml
layers:
  model:
    path: abicheck/model
    may_import: []
  storage:
    path: abicheck/storage
    may_import: [model]
  extract:
    path: abicheck/extract
    may_import: [model, storage]
  compare:
    path: abicheck/compare
    may_import: [model]
  policy:
    path: abicheck/policy
    may_import: [model, compare]
  workflows:
    path: abicheck/workflows
    may_import: [model, storage, extract, compare, policy]
  report:
    path: abicheck/report
    may_import: [model, compare, policy, workflows]
  frontends:
    path: abicheck/frontends
    may_import: [model, workflows, report]
```

`debt.yaml` is the temporary migration ledger. Each entry records at least:

```yaml
files:
  - path: abicheck/aggregate.py
    baseline_lines: <measured-at-adoption>
    target: workflows/aggregate
    rule: no_growth
    category: workflow_monolith
    owner: <team-or-maintainer>
    rationale: <why-this-cannot-move-in-phase-0>
    review_by: <date>
```

The implementation must measure baselines from the adoption commit; this ADR
does not hard-code guessed line counts. `modules.yaml` should remain stable as
the desired architecture. `debt.yaml` should shrink toward empty and must not
become a permanent import allowlist.

### D13. A focused architecture check enforces the contract

Add `scripts/check_architecture.py` and route it through the existing
`scripts/verify.py` step catalog. It enforces:

- hard line limits for new files;
- no growth of recorded large files;
- no new forbidden root prefix files or root implementation packages;
- declared cross-package dependency direction;
- no new responsibility-package cycles;
- facade size, explicit-export, and delegation-only constraints;
- no unclassified first-party imports from migrated packages;
- no flat module occupying a target package name;
- scoped instruction presence for created responsibility packages; and
- schema and path validity for `modules.yaml` and `debt.yaml`.

The checker reports a precise import edge, file, and violated rule. It does
not bury architecture enforcement inside a growing generic readiness script;
that script may invoke the focused check but does not reimplement it.

The initial version operates only on migrated responsibility packages plus
new files and debt baselines. It must not claim the legacy flat tree already
conforms. Tightening coverage is a migration deliverable and is visible in
the debt ledger.

## Current-to-target ownership map

| Current area | Target owner |
|---|---|
| `aggregate.py`, `aggregate_findings.py`, `aggregate_manifest.py` | `workflows/aggregate`, with report projection in `report` |
| `bundle.py`, `bundle_analysis.py`, release comparison code | analysis in `compare/bundle`; discovery and fan-out in `workflows/release` |
| `buildsource/inline.py` | shared values in `model`; extraction in `extract`; orchestration in `workflows/artifact` |
| `buildsource/source_graph.py` | graph values in `model`; construction in `extract/source`; comparison in `compare` |
| `dumper_castxml.py`, `dumper_clang.py` | `extract/headers/castxml` and `extract/headers/clang` |
| `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py` | `extract/binary` |
| `diff_*`, `checker.py`, `comparability.py`, `finding_identity.py` | `compare` |
| `change_registry.py` | declarative `model/change_catalog`; classification algorithms in `policy` |
| assurance, suppression, severity, and contract configuration | `policy` |
| `reporter.py`, `reporter_markdown.py`, HTML/SARIF/JUnit modules | `report/document`, `report/build`, and `report/render` |
| compare/dump/scan service pipelines | `workflows` |
| `cli.py`, `cli_*` | `frontends/cli`; root `cli.py` becomes a facade |
| `service.py` | public facade over `workflows` |
| `compat/cli.py` | retained namespace delegating to `frontends/compat` |
| serialization, snapshot I/O, caches, and baselines | `storage` |
| `scripts/check_ai_readiness.py` | thin orchestration plus focused checks under `scripts/quality/` where appropriate |
| historical/known-gap material in root instructions | ADRs, issues, architecture docs, or generated facts according to content type |

This table routes responsibilities; it does not require one commit per row or
authorize a mechanical move without behavior tests.

## Implementation plan

### Phase 0 — stop new debt

1. Accept this ADR and add the compact task-routing/dependency contract to
   root guidance.
2. Add `architecture/modules.yaml`, `architecture/debt.yaml`, and their
   schema/documentation.
3. Inventory existing oversized and prefix-family modules, record measured
   no-growth baselines, owners, targets, rationales, and review dates.
4. Implement `scripts/check_architecture.py` for new-file limits, frozen root
   families, debt no-growth, and dependency checks over migrated packages.
5. Register the focused check in `scripts/verify.py` and add focused unit
   tests for valid and invalid miniature trees.
6. Keep the old 2,000-line check as a temporary final backstop.
7. Reduce tool-specific instruction adapters to pointers. Shorten the root
   instructions only after durable historical material has an explicit new
   home; do not delete unique operational knowledge during cleanup.

**Acceptance:** a new forbidden prefix sibling, an oversized new module, an
undeclared responsibility import, or growth of a debt-tracked file fails the
focused check with an actionable message. Existing debt remains runnable.

### Phase 1 — prove the pattern with aggregation

Create real implementation modules, not empty scaffolding:

```text
workflows/aggregate/
  contracts.py
  resolve.py
  load.py
  fold.py
  reconcile.py
  execute.py

report/
  aggregate.py
```

Move typed contracts and behavior with their tests. Switch internal callers
immediately to the new owner. Retain in `aggregate.py` only documented public
exports that require compatibility. The new package cannot import the old
facade.

**Acceptance:** semantic results and JSON are exactly compatible; public
imports covered by contract tests continue to work; internal imports use the
new owner; no reverse facade import or duplicated aggregation decision
exists; the relevant debt entries shrink or disappear.

### Phase 2 — establish the canonical report document

Implementation status: the immutable, JSON-shaped ``ReportDocument`` and its
pure JSON projection are established, and all native JSON report modes (full,
stat, leaf, and root-cause) now cross that boundary. Markdown, HTML, SARIF, and
JUnit remain explicit follow-up slices; this partial status must not be read as
the phase acceptance criteria having been met.

1. Define immutable `ReportDocument` contracts from existing report-model
   behavior rather than inventing a second schema.
2. Build the document once from a workflow result.
3. Route JSON and Markdown first, then HTML, SARIF, and JUnit, through pure
   projections.
4. Move all filtering, severity, verdict, and gate decisions before document
   construction.
5. Delete output-specific verdict repair and post-render mutation after
   parity tests cover every format.

**Acceptance:** all renderers consume one document; format parity and golden
tests pass; mutability tests show renderers cannot alter the workflow result;
no renderer computes an exit code or compatibility decision.

### Phase 3 — converge artifact workflows

Use the pattern already emerging in the typed compare, dump, input-resolution,
and artifact-plan code:

```text
ArtifactRequest -> ResolvedArtifactPlan -> ArtifactResult
```

Route dump, both compare sides, scan candidate/baseline, release fan-out,
application compatibility, and dependency comparison through shared
per-artifact resolution and execution contracts. Pair-wide decisions remain
in the pair workflow; single-input resolution does not acquire artificial
knowledge of both sides.

**Acceptance:** equivalent CLI and typed-API requests resolve equivalent
plans; extraction occurs once per artifact; resource lifetimes cover execution;
dry-run renders the same resolved plan normal execution consumes; achieved
depth and degradation are result facts rather than frontend guesses.

### Phase 4 — thin CLI and Python API

1. Move command input translation into `frontends/cli/commands` and reusable
   Click-only option declaration into `frontends/cli/options`.
2. Make workflows the sole operation owners and reports the sole rendering
   owners.
3. Reduce root `cli.py` to command registration/delegation and root
   `service.py` to documented typed functions.
4. Derive every frontend's process response from the same `GateDecision`.
5. Update tests to patch implementation owners, retaining facade tests only
   for supported public imports and delegation.

**Acceptance:** both root facades are below 150 lines, declare `__all__`, and
contain no product logic; frontend modules contain no extraction or policy
algorithm; CLI/API parity tests exercise shared workflows.

### Phase 5 — parsers and catalogs

After lower-level model contracts stabilize:

1. split CastXML and Clang parsing by entity and shared parser context;
2. separate source-graph values, construction, and comparison;
3. partition the change catalog by taxonomy and validate one assembled
   registry; and
4. remove superseded private re-exports, migration edges, and cycle
   exceptions.

**Acceptance:** parser fixtures demonstrate byte/fact parity where applicable;
catalog validation proves global uniqueness and complete metadata; no parser
imports policy/report/workflows/frontends; corresponding debt entries are
removed.

## Migration rules for every phase

Each migration PR must be a vertical, behavior-preserving slice and must:

1. identify the old owner, new owner, supported public paths, and debt entry;
2. move implementation and its unit tests together;
3. switch internal callers to the new implementation module in the same PR;
4. leave only necessary public delegation in the old module;
5. add or update compatibility-import tests for retained public paths;
6. prove no new package imports the old facade;
7. preserve output/schema behavior unless the PR separately declares and
   tests a product change;
8. update `modules.yaml` coverage and shrink/remove `debt.yaml` entries; and
9. run the canonical PR verification profile.

Line-count reduction without ownership transfer does not satisfy a phase.

## Alternatives considered

### Keep the flat package and enforce only a lower line limit

Rejected. It encourages more prefix siblings and mechanical splits while
leaving ownership and dependency direction undefined. The repository would
have smaller files with the same coupling graph.

### Split every oversized module immediately

Rejected. A mass move creates review noise, import churn, and compatibility
risk before target contracts are enforceable. Incremental vertical slices
allow parity tests and facade decisions per responsibility.

### Preserve every old private import and monkeypatch location

Rejected. That makes incidental implementation paths permanent and forces
new packages to import through legacy owners. Only documented public paths
receive compatibility treatment; internal tests move to the real owner.

### Use one broad `core` package

Rejected. `core` would reproduce the current ambiguity inside a directory.
The eight packages are based on decisions and data transformations, not a
generic notion of importance.

### Allow dependency cycles during migration

Rejected as a stable policy. Explicit, expiring debt records can describe a
temporary edge, but the target graph remains acyclic and no permanent
allowlist is created.

### Create the full destination tree up front

Rejected. Empty directories communicate false progress and create package
surfaces with no owner. A package appears when implementation and tests move.

### Put all architectural checks into `check_ai_readiness.py`

Rejected. Architecture validation is one focused concern with its own
configuration and tests. The verification orchestrator should invoke it, not
absorb its implementation.

## Consequences

### Positive

- The filesystem answers where new behavior belongs.
- Cross-package dependencies become reviewable and machine-checkable.
- Typed stage boundaries reduce duplicate resolution and frontend drift.
- Compatibility obligations are explicit rather than inferred from every
  historical internal import.
- Report formats cannot silently disagree about findings, verdicts, or gates.
- Agent guidance becomes shorter because ownership moves into the tree and
  scoped package contracts.
- Debt is visible as temporary data with owners and review dates rather than
  normalized by an ever-increasing maximum file size.

### Costs and risks

- Migration temporarily increases the number of facade and target modules.
- Import-path churn can disrupt tests and external users if public/private
  boundaries are not explicitly audited.
- An over-eager dependency checker can misclassify dynamic or optional
  imports; its tests and errors must distinguish unsupported edges from
  parser limitations.
- `ReportDocument` migration may expose format-specific decisions that have
  accidentally diverged and require deliberate reconciliation.
- Reducing root instructions requires careful relocation, not deletion, of
  unique operational knowledge.
- Until all debt entries are retired, contributors must understand both the
  target architecture and explicitly recorded legacy exceptions.

## Definition of done

The repository-wide migration is complete when:

1. `abicheck/` root contains only entry points, supported facades/public
   modules, and responsibility packages.
2. No new root `cli_*`, `service_*`, `reporter_*`, `diff_*`, or equivalent
   pseudo-package sibling exists.
3. No ordinary new production module exceeds 800 lines.
4. Existing files above 800 lines cannot grow without an explicit reviewed
   debt-baseline change.
5. Every cross-package import follows `modules.yaml`.
6. No responsibility-package dependency cycle exists.
7. Root `cli.py` and `service.py` are delegation-only facades below 150 lines.
8. Every major operation follows `Request -> ResolvedPlan -> Result`.
9. Dry-run renders the actual resolved plan.
10. Every output format consumes one immutable `ReportDocument`.
11. Extraction cannot import policy, report, workflows, or frontends.
12. Compare cannot decide suppression, severity, or exit status.
13. Renderers cannot alter findings, verdicts, or gate state.
14. Root `AGENTS.md` is a stable routing contract below 350 lines and scoped
    package instructions exist for every responsibility package.
15. A contributor adding an ELF fact, detector, policy rule, CLI flag, or
    report field can identify its owner from one routing table without first
    opening a legacy monolith.
16. `architecture/debt.yaml` is empty or contains only explicitly accepted
    exceptions that are no longer described as migration work.

The immediate deliverable after acceptance is Phase 0: establish ownership,
contracts, scoped guidance, and no-growth enforcement. Splitting another
large file before those constraints exist is not progress toward this ADR by
itself.
