# ADR-061: Responsibility-Package Architecture and Flat-Namespace Migration

**Date:** 2026-08-24
**Status:** Accepted — partially implemented. The named foundation and
migration slices in Phases 0, 1, 2, 3, and 5 have landed; Phase 4 and
repository-wide convergence remain incomplete. A landed phase label means
the slices that phase named closed — **not** that the repository-wide
guarantee behind them holds. Each phase record under "Implementation plan"
states its own scope qualification, and the outstanding acceptance gaps
(real dependency enforcement, canonical result/report convergence,
supported-facade cleanup, storage/model ownership, and retirement or
explicit acceptance of legacy ownership debt) are stated once in
[Remaining acceptance gaps](#remaining-acceptance-gaps), worked in the
bounded [closure sequence](#closure-sequence) that replaces the open-ended
Phase 4 narrative. Closing the last facade-size item would not by itself
satisfy the [definition of done](#definition-of-done).
**Authority:** product capability placement follows
[ADR-068](068-one-comparison-product-and-scan-retirement.md); result
semantics follow the repository-root `vision.md` and its workstreams
([`vision-api-abi-evolution.md`](../plans/vision-api-abi-evolution.md));
storage-format work is coordinated with
[ADR-062](062-project-snapshot-storage-v2.md) and
[ADR-063](063-one-semantic-pipeline.md). This ADR owns implementation
ownership and dependency boundaries only.
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
    cli/commands/{dump,compare,aggregate,release}.py
    cli/options/{evidence,compiler,policy,output}.py
    compat/abicc.py

  compat/                      retained public namespace; delegation only
```

There is deliberately no `workflows/scan/` or `cli/commands/scan.py` in this
layout: [ADR-068](068-one-comparison-product-and-scan-retirement.md) retires
the command, and its surviving capabilities belong to the compare workflow
(D7). Until that retirement lands, `scan`'s existing modules stay where they
are and are not migrated into a permanent home.

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

**A dynamic import is still a dependency.** An `importlib.import_module`
call naming a known first-party module is an import edge, whatever an AST
scan of `ast.Import`/`ast.ImportFrom` nodes can see. Deferring resolution
changes *when* the dependency is satisfied, never which layer depends on
which, so a dynamic bridge introduced specifically to keep a forbidden
direction out of the checks is unresolved debt with an owner and an expiry —
never evidence that a migration closed. Genuinely dynamic or plugin-style
loading is the narrow, documented exception; the architecture check is
expected to resolve literal `import_module("...")` calls and simple aliases
of them as edges. Today's bridges are catalogued in
[gap A](#a-real-dependency-violations-not-their-visibility-to-the-checker).

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
class CompareRequest:
    old: ArtifactInput
    new: ArtifactInput
    scope: ComparisonScopeSelection
    configuration: EvaluationConfiguration

@dataclass(frozen=True)
class ResolvedComparePlan:
    old_plan: ResolvedArtifactPlan
    new_plan: ResolvedArtifactPlan
    acquisition: ScopeAcquisitionRecord
    effective_configuration: EffectiveEvaluationConfig

@dataclass(frozen=True)
class CompareResult:
    comparison: ComparisonResult
    decision: ExitDecision
    coverage: CoverageSummary
    timings: StageTimings
```

The plan carries selection and acquisition state, not just two resolved
operands: "which members were selected, which were acquired, and which were
expected but never reached a completed comparison" is a resolved fact
(ADR-065), not frontend orchestration.

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
def run_compare(request: CompareRequest) -> CompareResult:
    with resolve_compare_request(request) as plan:
        return execute_compare_plan(plan)
```

`scan` is deliberately **not** the example here, and is not a future owner of
this shape: [ADR-068](068-one-comparison-product-and-scan-retirement.md)
retires the command and moves its capabilities onto the one comparison
product. Do not migrate a command scheduled for retirement into a permanent
contract — route its surviving capabilities to the compare workflow instead.

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

**A supported public path and an owning package are two separate answers.**
`architecture/modules.yaml`'s `public_root_surfaces` exempts a *caller* from
needing the imported module to be classified; it does not answer who owns
the implementation behind that path, and a module on the list still needs a
real owner. Canonical internal callers reach the owner, not the
compatibility route. Conflating the two is
[gap B](#b-public-compatibility-surfaces-separated-from-ownership-exemptions).

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
| compare/dump service pipelines | `workflows` |
| `scan`'s surviving capabilities | the compare workflow, per [ADR-068](068-one-comparison-product-and-scan-retirement.md) — not a `workflows/scan` package |
| `bundle_facts.py` and its serialization/store siblings | values in `model`; persistence in `storage`; capture/comparison orchestration in `workflows` |
| `probe_harness.py`'s snapshot (de)serialization | `storage`, reached from a workflow — comparison does not own persistence |
| `cli.py`, `cli_*` | `frontends/cli`; root `cli.py` becomes a facade |
| `service.py` | public facade over `workflows` |
| `compat/cli.py` | retained namespace delegating to `frontends/compat` |
| serialization, snapshot I/O, caches, and baselines | `storage` |
| `scripts/check_ai_readiness.py` | thin orchestration plus focused checks under `scripts/quality/` where appropriate |
| historical/known-gap material in root instructions | ADRs, issues, architecture docs, or generated facts according to content type |

This table routes responsibilities; it does not require one commit per row or
authorize a mechanical move without behavior tests. Rows still carrying
legacy implementations need a recorded **disposition** — migrate, retain as
a supported public module, or accept an explicit exception — rather than
remaining unclassified indefinitely; see
[gap F](#f-a-repository-wide-completion-track).

## Implementation plan

Each phase below is a **closure record**, not a chronology: what landed, what
the label does and does not mean, the durable lessons worth not
rediscovering, and the phase's own acceptance criteria. Active completion
tracking lives once, in
[Remaining acceptance gaps](#remaining-acceptance-gaps) and the
[closure sequence](#closure-sequence), rather than inside each phase's
narrative — D11's rule applied to this document itself. The per-PR
investigation history these sections used to carry (review rounds,
superseded measurements, blockers that later re-measured away) is in git;
the last full-length revision is `cfda6774`. The technical findings that
outlive their PR are either summarized under each phase's "durable lessons"
here or already mirrored into
[known gaps](../known-gaps.md), their canonical home.

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

**Landed.** `report/document.py`'s immutable, JSON-shaped `ReportDocument`
exists, and every output format — JSON, SARIF, JUnit (via
`report/render_xml.py`), `--stat`, HTML (`html_report.build_html_document` +
`report/render_html_document.py`), and all four Markdown views
(`report/render_markdown_document.py`, `report/render_markdown_alternate.py`)
— builds one and projects it. Every format also has the fact-vs-formatting
split applied: a `compute_*` half that reads the `DiffResult` and decides,
and a `render_*` half that formats and decides nothing. Item 4 (decisions
before construction) closed for both halves it names — the gate decision
through `policy/gate_decision.gate_decision_for_result`, the per-finding
verdict through `report/finding.py`'s `ReportFinding` — and item 5
(post-render mutation) closed for every fold, including the scoped-gate JSON
fold, now `report/scoped_gate.apply_scoped_gate` operating on the still-
mutable dict before the single render.

**What the label does not mean.** Each format builds *its own* document from
the `DiffResult`; `service_render.render_output` still dispatches the result,
snapshots, severity configuration, and presentation options down six
independent paths. That is a per-format boundary, and it is real — but
wrapping six separately-built projections in the same immutable type is not
proof that they cannot disagree. The stronger invariant this ADR's
definition of done actually states — *one completed evaluation → one
completed semantic report document → every format* — is gap C below, and is
[`duplication-and-convergence-assessment.md`](../plans/duplication-and-convergence-assessment.md)
Phase 4's `ReportEnvelope` target rather than a second design.

**Gap C status (2026-09-07, updated through the JUnit slice — all five
named formats now converged): JSON, Markdown-full/review, HTML's default
view, SARIF's default view, and JUnit's default view all converge onto the
shared choke point (HTML, SARIF, and JUnit only partially — see their own
progress updates below, and the JUnit slice's overall closure summary
further down this section); Markdown-leaf/root-cause stays its own
separate, legitimate document by design (see the scope decision below).**
`report/build.py`'s
`build_report_document(result, ...)` is now the
single function that performs the full `report_mode="full"` build
(`_build_json_base`, `_add_abi_surface_breakdown`, `_add_changes_block`, the
gate decision, the side-facts fold, etc.) — moved out of `reporter.to_json`'s
own inline body (now a thin `build -> render_json` wrapper) and out of
`service_render._render_json_output` (which calls it directly for
`report_mode="full"`, bypassing `to_json` entirely). Verified against the
pre-refactor path via `tests/unit/report/test_build_report_document.py`
(byte-identical JSON for both the default and `show_only`-filtered cases,
plus a mock-based assertion that `render_output("json", ...)` calls the
shared build exactly once and never falls back to the legacy `to_json`
pipeline for full-mode JSON). `to_json`'s `--stat`/`leaf`/`root-cause`
report modes are **not** routed through this choke point yet — they remain
each their own independent build, same as before this change; only
`report_mode="full"` (the default) was covered by that first slice.

**Progress update (2026-09-07, later same day): full-mode Markdown and
`review` (unconditional-recommendation markdown) now also route through the
one shared build.** `service_render.render_output()`'s markdown branch
(only for `report_mode == "full"` — `--stat` and the Markdown
`leaf`/`root-cause` alternate views deliberately do not, see the scope
decision below) and its `review` branch each now call
`build_report_document(result, ...)` exactly once and thread the resulting
`ReportDocument` down through `to_markdown`/`to_review_digest` into
`report/render_markdown_document.py`'s `build_markdown_document`/
`build_review_digest_document` (both gained an optional `report_document`
parameter; a direct caller passing none keeps the prior, independent-build
behaviour, so this is additive, not a signature break). Those two functions
now reuse the shared document's `disposition_audit` field instead of a
second, independently-resolved call to `compute_disposition_audit`.
Everything else full-mode Markdown/`review` render (the headline table,
policy section, severity groupings, confidence section, and so on) stays
computed the way it already was, reading `DiffResult` directly through
`reporter_markdown.py`'s existing `compute_*` functions — deliberately, not
an oversight: on inspection, none of it was actually a second, independently
*decided* value at risk of drifting from JSON's own decision. Every
classification these Markdown sections rely on (`categorize_changes`,
`gate_eligible_changes`, `apply_show_only`, `_suppress_dangling_correlation_
notes`, `impact_for`) was already the identical shared pure function JSON's
own build calls, not a parallel reimplementation — two calls to the same
deterministic function of `(result, ...)` cannot disagree, so the
byte-for-byte-safe, real convergence available here was structural (route
through one call, reuse what the shared document already carries in a
matching shape) rather than a rewrite of every Markdown section to read
JSON-shaped fields it doesn't have a matching presentation for
(`disposition_audit` is the one field whose shape matches exactly;
`headline`/`policy`/`severity_groups`/etc. have no JSON-document counterpart
at all, since JSON never renders them in that shape). Verified via
`tests/` markdown/review suites plus the full golden suite, all byte-
identical to pre-change output (see this ADR's own PR history / the
`duplication-and-convergence-assessment.md` plan for the exact commit).

**Progress update (2026-09-07, later still the same day): HTML's default
view now also routes through the one shared build, to the same depth as the
Markdown/review slice.** `service_render.render_output()`'s `html` branch
now calls `build_report_document(result, show_only=show_only,
show_impact=show_impact, severity_config=severity_config)` once and forwards
the resulting `ReportDocument` into `html_report.generate_html_report` /
`build_html_document` (both gained an optional `report_document` parameter,
additive — a direct caller passing none keeps the prior, independent-build
behaviour). `build_html_document` reuses the shared document's
`disposition_audit` field (reconstructed via `DispositionAudit.from_dict`,
the same round-trip Markdown's own `report_document` handling already uses)
at both of its two call sites — the `compat_html` ABICC-clone layout's own
disposition-audit block, and `compute_summary_table`'s audit argument —
instead of two independent calls to `compute_disposition_audit` over the
same ledger. HTML's remaining facts (bucketing changes into removed/changed/
added, the per-section `ChangeRow` tables, `compat_html`'s ABICC severity-band
bucketing, the gate/scoped-verdict cards) were read in full while doing this
work and confirmed to be exactly the gap the prior assessment already
recorded: JSON's flat `changes[]` array plus summary `severity` block has no
matching shape for any of them today, so converging them would mean adding
new fields to the shared document first (the "genuinely new shared-document
design" the assessment below already named for HTML) — deliberately not
attempted in this slice, same reasoning as `severity_groups` staying
Markdown-side. Also closed in this slice: the ABICC-clone `compat_html=True`
layout previously had no golden test at all (a gap C acceptance-criteria
item this slice was asked to close alongside the wiring above); it now
has one (`tests/golden/html_template/main_report_compat.html`,
`tests/test_html_template_golden.py`), verified byte-identical on every
pre-existing case and passing on the new one. Verified via the HTML test
suite, the full golden suite (including the new `compat_html` case), and the
usual ruff/mypy/ai-readiness/architecture gates, all clean.

**Progress update (2026-09-07, SARIF slice): SARIF now also routes through
the one shared build, same structural depth as the Markdown/review and HTML
slices.** `service_render.render_output()`'s `sarif` branch now calls
`build_report_document(result, show_only=show_only,
severity_config=severity_config)` once (unconditionally, independent of
`report_mode` — SARIF's own `report_mode="root-cause"` only adds extra
per-result properties on top of the same shape, unlike Markdown's genuinely
separate leaf/root-cause documents, so there is no reason to gate the shared
build on it) and forwards the resulting `ReportDocument` into `sarif.
to_sarif`/`to_sarif_str` (both gained an optional `report_document`
parameter, additive only — a direct caller with none keeps the prior,
independent-build behaviour). `to_sarif` reuses the shared document's
`disposition_audit` field (read directly off `report_document.to_mapping()`,
already JSON-shaped so no reconstruction step is needed — unlike HTML's/
Markdown's own `DispositionAudit.from_dict` round-trip) instead of an
independent `compute_disposition_audit` call for its `properties.
dispositionAudit` block. SARIF's own shape — the rule catalog (`rules_seen`),
per-result `level`/`location` derivation, root-cause grouping, and the
`scopedGate`/`severityGate`/coverage-notification blocks — was re-read in
full during this slice and confirmed to still be exactly the gap the prior
assessment already recorded: none of it has a matching field in
`build_report_document`'s JSON-shaped structure today, so genuinely
converging it means adding new shared-document fields first, deliberately
not attempted here — same reasoning as HTML's and Markdown's own remaining
facts. `sarif.py` already had SARIF's own compute/render split in substance
(`to_sarif` computes the SARIF dict; `to_sarif_str` composes it with
`report.render_json.render_mapping_as_json`, the same generic JSON-freeze-
and-render step SARIF's own `report/AGENTS.md` entry already names) — this
slice's job was wiring the shared build into the existing split's compute
half, not inventing a new one. Verified via the SARIF test suite (220
passed), a new `TestSarifReusesSharedDocument` class in
`tests/unit/report/test_build_report_document.py` (byte-identical
`to_sarif_str` output with and without a supplied `report_document`, equal
`dispositionAudit` values, and a `render_output("sarif", ...)`-calls-the-
shared-build-exactly-once assertion for both `report_mode="full"` and
`"root-cause"`), the full golden suite, the HTML template golden (shared-
code regression tripwire), and the usual ruff/mypy/ai-readiness/architecture
gates, all clean.

**Progress update (2026-09-07, JUnit slice — the last of the five named
formats): JUnit now also routes through the one shared build, same
structural depth as HTML's/SARIF's own slices.**
`service_render.render_output()`'s `junit` branch now calls
`build_report_document(result, show_only=show_only,
severity_config=severity_config)` once, unconditionally (independent of
`report_mode`, matching SARIF's own reasoning: JUnit's own `"root-cause"`
mode only adds `rootCauseId`/`rootCause` attributes to each `<failure>` on
top of the same shape, it does not restructure the per-symbol `<testcase>`
tree), and forwards the resulting `ReportDocument` into `junit_report.
to_junit_xml`/`_build_testsuite` (both gained an optional `report_document`
parameter, additive only — a direct caller with none keeps the prior,
independent-build behaviour). `_add_disposition_audit_properties` reuses
the shared document's `disposition_audit` field via the same
`disposition_audit_dict_reusing_document` helper SARIF's slice introduced,
instead of an independent `compute_disposition_audit` call for its
`abicheck.detected_total`/`abicheck.effective_total`/
`abicheck.disposition.*` testsuite properties.

JUnit's own remaining facts were re-read in full during this slice and
confirmed to be exactly the gap the prior assessment recorded, with one
addition the prior assessment did not have available to check yet: JUnit's
per-finding verdict/category resolution (`_is_failure`/`_failure_type`) was
*already* routed through `report.finding`'s `ReportFinding`/
`build_report_findings` primitive by an earlier slice (ADR-061 Phase 2 item
4b) — the same canonical primitive a full convergence would want — but as a
*separate* call from what `build_report_document` computes, not a value
read off the shared document, because `build_report_document`'s own
`_add_changes_block` does not itself build a `ReportFinding` set at all
(JSON's `changes[]` entries resolve each change's verdict inline via
`effective_verdict_for_change`, with no per-finding `IssueCategory` in the
JSON shape at all today). Threading that through would mean either (a)
rewriting JSON's own `_change_to_dict` to compute and carry `IssueCategory`
too — a change to a format whose output this slice must leave
byte-identical — or (b) inventing a second, JSON-object-external field on
`ReportDocument` keyed by `finding_id` for a fact only JUnit needs; neither
is the safely-mechanical, already-shaped substitution this slice's own
scope is (see "What remains open" below). JUnit's symbol/testcase tree and
its root-cause grouping are, as before, its own SARIF/JUnit-shaped
computation with no shared-document counterpart.

Verified via the JUnit test suite (156 passed), a new
`TestJunitReusesSharedDocument` class in `tests/unit/report/
test_build_report_document.py` (byte-identical `to_junit_xml` output with
and without a supplied `report_document`, equal disposition-audit testsuite
properties, and build-called-exactly-once assertions for both
`report_mode="full"` and `"root-cause"`), the full golden suite, the HTML
template golden (shared-code regression tripwire), and the usual
ruff/mypy/ai-readiness/architecture gates, all clean.

Two acceptance tests from the original gap-C task were added in this
slice, now that all five formats cross the shared-document boundary: a
`TestRendererOrderIndependence` class rendering the same completed
`DiffResult` through json/html/sarif/junit/markdown in two different
orders and asserting each format's own output is byte-identical regardless
of order, with `build_report_document` (mock-spied, `wraps=` the real
function) called exactly once per format render in either order; and a
`TestSarifAndJunitDecisionBoundary` class, the honest, narrower sibling of
`test_render_html.test_render_html_imports_no_decision_making_module` for
these two formats — see that class's own docstring for why an import-based
"no decision module reached at all" guard would be false for `sarif.py`/
`junit_report.py` as currently structured (neither has HTML's real
compute/render module split yet), and what the real, current boundary it
asserts instead is (the disposition-audit reuse path specifically, checked
both by call-count and by an AST scan of the actual call site).

**What remains open.** Markdown's `leaf`/`root-cause` alternate views (see
the scope decision immediately below — these are separate, legitimate
documents, same reasoning as JSON's own `leaf`/`root-cause`/`--stat`, not an
oversight left out of any slice), HTML's own bucketing/section/compat-mode
computation, SARIF's own rule-catalog/level-derivation/root-cause/
scoped-gate computation, and JUnit's own per-finding verdict/category
resolution, symbol/testcase tree, and root-cause grouping (see the
respective progress updates above — every format's shared build call itself
has now landed; only `disposition_audit` reuse was safely available beyond
that for any of the three non-JSON/Markdown formats). None of this is an
oversight: each is genuinely format-specific presentation, or would require
a genuinely new shared-document field this initiative deliberately declined
to invent mid-slice — see each progress update's own reasoning.

**Gap C overall closure state, across all five named formats (JSON,
Markdown-full/`review`, HTML, SARIF, JUnit) — 2026-09-07, JUnit slice, the
last of the five.** What is genuinely converged: the *decision* layer —
per-finding verdict/category (`report/finding.py`'s `ReportFinding`, used
directly by JSON's severity JSON and by JUnit, wherever it is used, computed
via the one canonical primitive), the gate decision
(`policy/gate_decision.gate_decision_for_result`), and the disposition audit
(`report/disposition_audit.py`'s `compute_disposition_audit`) — is resolved
exactly once per render via `build_report_document` and *reused*, never
re-derived, by JSON, Markdown/`review`, HTML, SARIF, and JUnit alike, for
every fact each format actually reuses today. What remains legitimately
format-specific presentation, precisely stated per format (not overclaimed):
Markdown's `severity_groups` headed-section grouping and its `leaf`/
`root-cause` alternate views; HTML's `removed`/`added`/`changed` bucketing,
per-section `ChangeRow` tables, `compat_html`'s ABICC severity-band
bucketing, and its `nav_bar`/`summary_table`/`gate_card`/`scoped_verdict`
dataclasses; SARIF's rule catalog, per-result `level`/location derivation,
root-cause grouping, and `scopedGate`/`severityGate`/coverage-notification
blocks; and JUnit's per-finding verdict/category resolution (itself already
routed through the canonical `ReportFinding` primitive, just not read off
the shared document — see the JUnit progress update above for exactly why),
symbol/testcase tree, and root-cause grouping. As the HTML and Markdown
slices' own reports already found and this slice reconfirms for SARIF and
JUnit: most of each format's own section/layout logic was never actually a
second, independently-*decided* value at risk of drifting from another
format's — it is presentation-only computation over already-agreed facts,
which is what "converged" means in this ADR's sense (*cannot disagree on a
decision*), not "byte-identical internal implementation" across formats.
Closing any of the items in the paragraph above for real would mean adding
a new field to `ReportDocument` for that format's own shape first (as each
format's own progress update says), which is deliberately out of scope for
this initiative's five slices — a further, separately-scoped piece of work
if a future session judges it worth doing.

**Durable lessons.**

- A renderer that performs a registry lookup (`report_classifications`,
  `checker_policy.impact_for`) is deciding, not formatting. The rendered
  bytes are identical either way, so no golden test can catch it; the guard
  is an AST scan of the renderer's own imports
  (`test_render_html_imports_no_decision_making_module`), verified to fail
  against the pre-fix module.
- A section's `None` (this section does not exist) is not its empty value.
  Collapsing the two renders an empty table for a result that carried no
  data at all.
- Do not cache a policy-resolved value on the mutable `DiffResult`.
  `report_findings_for` recomputes per call because a caller may mutate the
  result between two renders; removing the hazard beat invalidating a cache
  against every mutation surface.
- Filtering that looks like formatting stays compute-side when it is really
  a report decision — which summary rows are non-empty, which reclassify
  rules are still active (an expired waiver must not be disclosed as
  in effect).
- Every closure here was verified by capturing a byte-exact golden against
  the **pre**-refactor path first, plus a property test stating the contract
  a golden cannot (round-trip safety, render purity, escaping per field).
  Two paths (`to_review_digest`, `--report-mode root-cause`) had no golden
  at all before their slice; the fixture came first, not after.
- A whole-document projection that pushes its module past the 800-line
  new-file ceiling splits out a sibling (`render_html_document.py`,
  `render_markdown_alternate.py`). It never trims to fit.

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
no renderer computes an exit code or compatibility decision. Items 1, 4, and
5 are met per format; item 2's "once" — one document shared by every format
of one evaluation — is gap C.

### Phase 3 — converge artifact workflows

**Landed.** The `ArtifactRequest -> ResolvedArtifactPlan -> ArtifactResult`
shape is real: `workflows/artifact/contracts.py` holds the plan type,
`workflows/artifact/resolve.py` decides a plan without running it, and
`workflows/artifact/execute.py` runs one and reports what it achieved. All
three service pipelines (`service_dump_pipeline.py`,
`service_input_resolution.py`, `service_compare_pipeline.py`) have
`workflows` owners and are free of CLI imports; `dump` resolves its request
once above the `--dry-run` branch, so dry-run renders the plan execution
consumes rather than one that merely agrees with it. Both binary-format
paths (ELF under CLI cleanup phase two PR C, PE/Mach-O under ADR-063 Phase
1) now execute through the one shared `execute_dump_request`.

**What the label does not mean.** This is the typed-resolution foundation,
not universal frontend convergence. The shared request/plan types still do
not carry selection, inventory, or acquisition state (ADR-065's scope
model), so a package or multi-member comparison still assembles that in
command-level orchestration — gap D below, and the same gap
[`vision-api-abi-evolution.md`](../plans/vision-api-abi-evolution.md)
records against scope convergence. The PE/Mach-O migration is verified by
mock-based CLI/unit tests only: the layering claim is proven, an end-to-end
run against a real PE/Mach-O binary is not.

**Durable lessons.**

- A blocker recorded once goes stale as the tree moves. Re-measure before
  scoping work against it — three separate blockers in this ADR turned out
  to describe a tree that had since changed.
- Engine code that cannot import upward writes private copies instead. The
  depth ladder existed four times and the `abicheck_inputs/` guard three,
  each copy's own comment explaining that it was a copy. The coupling was
  already being paid for in duplication before anything moved; the fix is a
  leaf both sides may import (`evidence_depth.py`,
  `buildsource/pack_shape.py`), not a facade.
- Error contracts are part of the move and are preserved exactly, not
  tidied: `ValidationError` (usage, exit 64) and `SnapshotError`
  (operational, exit 1) mean different things to a CI consumer. Every code
  was measured against the real CLI, and the characterization tests were
  written and committed *before* the move.
- An engine module owns no output stream. `on_output` replaced a `quiet`
  flag that was only meaningful to a caller holding a stream.

Use the pattern already emerging in the typed compare, dump, input-resolution,
and artifact-plan code:

```text
ArtifactRequest -> ResolvedArtifactPlan -> ArtifactResult
```

Route dump, both compare operands, the release fan-out, application
compatibility, and dependency comparison through shared per-artifact
resolution and execution contracts. Pair-wide decisions remain in the pair
workflow; single-input resolution does not acquire artificial knowledge of
both sides.

**Acceptance:** equivalent CLI and typed-API requests resolve equivalent
plans; extraction occurs once per artifact; resource lifetimes cover execution;
dry-run renders the same resolved plan normal execution consumes; achieved
depth and degradation are result facts rather than frontend guesses. Met for
the single-artifact path; not met for selection, inventory, and acquisition
state (gap D).

### Phase 4 — thin CLI and Python API

**Landed.** `abicheck/frontends/` exists and holds the CLI: commands
(`frontends/cli/commands/{dump,compare}.py`), runtime (verbosity, output,
provenance, the exit decision), the option cluster
(`frontends/cli/options/*`), and `frontends/cli/moved.py`'s historical
import surface. Root `cli.py` went from 1,959 lines to a 140-line
registration facade. Classifying the whole `cli_*` family `frontends`
surfaced 47 real direction violations — the CLI reaching past the engine
into `policy`, `compare`, and `extract` — and all 47 were closed rather
than suppressed, each routed through a `workflows` re-export surface
(`workflows/gate.py`, `extraction.py`, `findings.py`, `scan_config.py`).
`ENGINE_CLI_BOUNDARY_ALLOWLIST` went 15 → 4 across Phases 3 and 4.
`policy_file.py` is now classified `policy`, unblocked by the structural
`PolicyFileProtocol`/`ReclassifyRuleProtocol` pair in
`model/policy_file_protocol.py`, which moved `compare_snapshots`,
`load_suppression_and_policy`, `_validate_contract_mode`, and
`dedup_policy_override_warnings` into `workflows/compare_policy.py` and took
`service.py` from 1,763 lines to 283.

**What the label does not mean — this phase is open.** `service.py` is 283
lines, not below 150, and the shortfall is no longer a blocker: it is nine
re-export blocks whose supported surface has not been audited, private
compatibility names retained for test patch locations (which D8 forbids),
and `scan`-shaped bindings whose future is ADR-068's to decide. Root
`cli.py` still registers `scan`, imports the legacy option hub, and applies
variant options at the *root* because the command module hit its size cap —
a placement decided by file-size pressure rather than ownership, which is
exactly what D5 says must not happen. That work is gap D and gap F below,
sequenced as closure packages 4 and 6.

**Durable lessons.**

- `checker_policy.py`'s model-vs-policy split is the hinge several other
  moves waited on: `ChangeKind` is defined there alongside real policy
  algorithms, so anything `model`-owned that must name a `ChangeKind` is
  blocked until it splits.
- The `PolicyFile` investigation's answer is a **structural `Protocol`**, not
  a subclass or a data-only base. Narrowing `DiffResult.policy_file` to a
  data-only type breaks every consumer that calls a method on it under the
  mypy gate, even though the runtime object is unchanged; a protocol
  satisfied structurally does not. Two mechanical requirements were each
  reproduced against `mypy --strict` before being trusted: collection
  members must be read-only `@property` declarations (a plain attribute is
  invariant and rejects `dict` against `Mapping`), and a protocol must
  declare the whole surface real callers use — including data attributes
  (`to_verdict`) and the exact `list` vs `Sequence` shape a downstream
  parameter demands. Reclassifying `policy_file.py` as `compare` was
  rejected outright: `compute_verdict` is policy logic by any reading, and
  mislabeling it only relocates the ambiguity this ADR exists to remove.
- `check_architecture.py` checks a file's own imports only when that file
  has a classification; `unclassified-import` additionally requires
  `migrated_source`. So a **flat, classified** module reporting zero
  findings says nothing about whether moving it is safe, and a deliberately
  unclassified leaf (`reclassify.py`) is exempt from every check by
  construction. Measure against the state after the move, not before it.
- Every hand-taken count in this phase went stale or was wrong at least
  once. Numbers here are re-measured or dropped, never carried forward.
- A `monkeypatch.setattr` against a name resolved through a lazy
  `__getattr__` rebinds nothing the real caller reads, and a re-export
  surface binds its names at import time. Both are ordinary Python
  semantics, and both make a facade's tests silently inert.
- Trimming a facade's explanatory comments to hit a line count transfers no
  ownership and does not satisfy the criterion.

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
algorithm; CLI/API parity tests exercise shared workflows. `cli.py` meets the
size criterion; `service.py` does not, and the size criterion is the *last*
check of this phase rather than its definition — reduce the coupling first
(closure package 6), then measure.

### Phase 5 — parsers and catalogs

**Landed — all four named items.**

1. *CastXML and Clang parsing split by entity and shared parser context* —
   closed on both backends (`extract/headers/castxml/*`,
   `extract/headers/clang/*`), with parity held by the shared `parse_*`
   surface behind `dumper._header_ast_parser`.
2. *Source-graph values, construction, and comparison separated* — closed
   for every internal caller off the `buildsource/source_graph.py` facade;
   the shared node/edge-classification predicates relocated into
   `model/source_graph_query.py`, and `template_graph.py` closed last via a
   split into `template_graph_fold.py`.
3. *Change catalog repartitioned* — all 397 entries moved into D9's
   `model/change_catalog/{symbols,types,platform,build,source}.py` taxonomy
   by which detector actually produces each kind, with all four
   registry-validation properties (global uniqueness, valid references,
   non-contradictory defaults, complete metadata) enforced. The eight empty
   flat siblings were deleted and `change_registry.py` is now a pure
   assembly point.
4. *Superseded private re-exports, migration edges, and cycle exceptions
   removed* — both slices landed with no stale allowlist entries remaining.

The `model` package and the `*_metadata.py` dataclass/parser split (each
format's facts in `model/*_facts.py`, re-exported by its parser) landed here
too — the split Phase 4 was blocked on.

**What the label does not mean.** Phase 5 closed the migrations it named; it
did not retire repository-wide legacy debt. The surviving flat *parser*
modules (`pe_metadata.py`, `macho_metadata.py`, `dwarf_metadata.py`,
`symvers_metadata.py`, and siblings) are still unclassified — their
`extract` classification is outstanding for every one of them — and the
storage/model ownership questions in gap E are untouched by it.

**Durable lessons.**

- A module that conflates a value with the code that produces it has no
  valid single classification. The split (`model/*_facts.py` +
  `extract/*_metadata.py`) is the general fix, and the same shape recurs for
  bundle facts and for snapshot persistence (gap E).
- A shared decoder two layers both need moves to an inward leaf both may
  import (`model/mangled_name.py`), for the whole codebase-wide call-site
  set — not reactively, from whichever caller tripped the gate first.
- Catalog completeness is enforced at construction, not reviewed: an entry
  with no `impact` fails at import time.

**Acceptance:** parser fixtures demonstrate byte/fact parity where
applicable; catalog validation proves all four of D9's properties; no parser
imports policy/report/workflows/frontends; the corresponding debt entries
are removed. Met for the four items named above.

## Remaining acceptance gaps

These are the differences between the landed phase slices above and this
ADR's own [definition of done](#definition-of-done). They are stated once,
here, rather than tracked inside each phase's narrative. Every one is a
dependency-and-ownership question; none of them is closed by a file getting
shorter.

### A. Real dependency violations, not their visibility to the checker

**Closure package 2 re-measured this gap rather than trusting the list
above** (this ADR's own repeatedly-learned lesson): a real, repo-wide AST
scan for every first-party `importlib.import_module("...")` call found 21
call sites, not the 6 this section used to name — ADR-063 track T10 had
already closed `report/render_markdown_document.py`'s and
`report/scoped_gate.py`'s bridges before this package started, and the
remaining 21 sort into three groups, not one:

- **A real, forbidden-direction evasion** (`workflows -> frontends`,
  workflows may not import frontends): `workflows/render.py` resolving
  `service_render.py` through `importlib.import_module("..service_render",
  __package__)` inside each function body — the exact shape this section
  used to describe. **Closed**: `workflows/render.py` retired; `service.py`
  (a flat, `workflows`-legacy-classified module, the one real caller) now
  imports `service_render` directly and statically. The edge itself is
  real and still crosses `workflows -> frontends` — retiring the bridge
  module made it visible, it did not make the direction legal — so it is
  recorded as a reviewed `dependency-direction` exception in
  `architecture/debt.yaml`'s new `dependency_direction_exceptions` (see
  below), not silently passing.
- **A second real, forbidden-direction evasion this re-measurement
  found that the original gap A text never named**:
  `cli_dump_helpers.py` (`frontends`) resolving `header_conditionals.py`
  (`extract`) the same way — frontends may only reach extract through
  workflows. **Closed the same way**: now a plain static re-export,
  recorded as a second `dependency_direction_exceptions` entry.
- **Legitimate same-layer or already-legal-direction bridges** — the
  other 19 call sites, all of the shape D6 already carves out
  ("genuinely dynamic or plugin-style loading is the narrow, documented
  exception") or the same-layer back-compat re-export shim
  `AGENTS.md`'s own "Moving helpers out of a module that re-exports
  them?" guidance recommends: `service.py`'s `service_header_scoped`
  binding, `workflows/input_resolution.py`'s `service_dump_native`
  binding, `comparability.py` <-> `comparability_profile.py`,
  `type_reachability.py` <-> `type_reachability_stdlib_spellings.py`,
  `serialization.py` <-> `bundle_facts_serialization.py`,
  `model/snapshot.py`'s `semantic_ir_legacy_adapter` assertion,
  `policy/public_surface.py`'s two split-module re-exports,
  `buildsource/source_graph.py`/`inline.py`/`template_graph.py`'s own
  split-module shims, `reporter_markdown.py` -> `report/
  dispatch_markdown.py`, `annotations.py` -> `annotations_step_summary.py`,
  `cli_buildsource.py` -> `cli_graph.py`/`cli_buildsource_helpers.py`,
  `cli.py`'s `MOVED`-table facade `__getattr__`, and
  `frontends/cli/commands/compare_bundle_facts.py`'s two bindings into
  `workflows`. Each one either stays inside one layer (so no direction is
  even at stake — the bridge exists purely to avoid growing the
  pre-existing, already-baselined `cli_buildsource`/`scan_engine` import
  cycle, or a same-layer back-compat split-module cycle) or crosses an
  *already-legal* direction (`frontends -> workflows`). None evades
  `check_architecture.py`'s direction check in the sense this gap is
  about; each evades only `check_ai_readiness.py`'s `import-cycle-growth`
  scan, which is deliberately a *different*, broader question (see the
  completion test below for why that scan is not widened here).
  `detector_registry.py`'s plugin-discovery loop and
  `policy/public_surface.py`'s dict-keyed target (a `Name`, not a string
  literal) are D6's own named "genuinely dynamic" exception outright — no
  literal target to resolve at all.

**The fix is composition at the outer boundary, not a better bridge** —
for the `service.py -> service_render` edge specifically, this remains the
correct target, not yet fully reachable in one pass. `workflows/render.py`
has retired, and the import is real, static, and visible; what has *not*
yet happened is `service.py` itself ceasing to be `workflows`-classified
for this one responsibility, since `service.py` is also imported, directly
off the flat facade, by three other `workflows`-classified modules
(`abicheck/l0_export_delta.py`, `abicheck/appcompat.py`,
`abicheck/service_scan.py`) that would need their own D6 migration onto
the real workflow owner first — reclassifying `service.py` today would
just move today's invisible-bridge problem into three *new*, real
`workflows -> frontends` edges at those call sites instead of closing it.
That migration is recorded as the accepted exception's own stated
follow-up, not attempted in this pass. The `cli_dump_helpers.py ->
header_conditionals.py` edge has the same shape: closing it for real needs
a `workflows`-owned wrapper `cli.py`'s and `frontends/cli/commands/
dump.py`'s call sites route through instead of naming the `extract`-owned
functions directly.

**Completion test — met for the direction check, deliberately not
extended to `import-cycle-growth`:** `scripts/check_architecture.py`'s
`_imports()` now resolves a literal `importlib.import_module("...")` call
(including a module-level alias such as `_importlib = importlib`, and the
`__import__("importlib").import_module(...)` chained form) as a real
import edge for the `dependency-direction` check, with focused unit tests
over miniature trees proving both directions: an evasion of a forbidden
direction fails, a same-layer or already-legal-direction bridge does not,
and a genuinely dynamic (non-literal) target is left alone rather than
guessed at. Every one of the 21 real call sites was re-checked against the
strengthened tool; the only two that turned into `dependency-direction`
findings are the two named above, both now resolved via the reviewed
`dependency_direction_exceptions` mechanism below rather than left dynamic
and unlisted.

`check_ai_readiness.py`'s `import-cycle-growth` scan is **deliberately not
widened** the same way, on reconsideration of the task as originally
framed: that scan's own docstring already documents the identical
`cli_buildsource -> cli_graph` shim as its intended, narrow escape hatch
("If you switch a shim like that to a static import, expect this gate to
flag the cycle... Fix the direction... instead"), and `AGENTS.md`'s own
"Moving helpers out of a module that re-exports them?" guidance
prescribes exactly this `importlib.import_module` pattern as the
*correct* way to preserve a back-compat re-export path without
recreating a real two-way file-level import cycle. Making that scan see
these edges would not surface a new architectural problem — every one of
the 19 legitimate bridges above is a deliberate, reviewed answer to a
real two-file cycle a split-for-file-size already created — it would
instead flag ~19 already-accepted, already-documented patterns across the
codebase as new cycle growth, which `AGENTS.md`'s own "Don't extend
`IMPORT_CYCLE_ALLOWLIST`... as a routine step" rule and this closure
package's "no new `IMPORT_CYCLE_ALLOWLIST` entries" constraint together
rule out fixing by allowlisting. Widening that scan is not this gap's
target (gap A is about *layer-direction* violations hidden from
`check_architecture.py`, D6's own framing); doing so anyway would trade a
closed gap for a large, unrelated wave of allowlist churn against a
policy this repository has already, deliberately, decided the other way.

**`architecture/debt.yaml`'s new shape**: `dependency_direction_exceptions`
is the ledger shape this section previously said closure package 2 owed —
distinct from the `files`/`no_growth` schema (`rule: "dependency-direction"`,
keyed by `(path, target)`, not a line-count baseline), holding exactly the
two edges above, each with an owner, a dated review, and a rationale
naming the follow-up migration that would close it for real.
`scripts/check_architecture.py` validates the new list's own schema (a
malformed entry suppresses nothing) and consults it only for the exact
`(path, target)` pair it names — every other`dependency-direction` finding
still fails the gate.

### B. Public compatibility surfaces separated from ownership exemptions

`architecture/modules.yaml`'s `public_root_surfaces` currently lets a
migrated package import `checker_policy`, `reclassify`, and `serialization`
without those modules having an owning layer. Two different questions are
being answered by one list: *is this import path supported for external
users?* and *which layer owns the implementation behind it?* A "yes" to the
first does not remove the need to answer the second.

`DiffResult` is the worked example. Its local override algorithm moved out
(`checker_policy.apply_policy_file_overrides`), so no policy algorithm runs
inside `checker_types.py` any more — but its methods still call
`checker_policy` and `reclassify`, both unclassified public-root leaves, so
the `model -> policy`-shaped dependency is separated in code without being
removed.

**The fix:** give the implementation behind each entry a real owner and keep
only the compatibility adapter at the public path; canonical internal
callers use the owner. This is *not* to be implemented by caching
policy-resolved values on today's mutable `DiffResult` — callers and tests
change policy after construction, and Phase 2's own lesson rejects that
cache. Preserve the mutation behavior at the supported legacy boundary and
move new internal processing onto completed, explicitly evaluated results.

**Completion test:** every module reachable through `public_root_surfaces`
names an owning layer; no canonical internal caller reaches an
implementation through the compatibility route; the supported import paths
still resolve, pinned by facade tests.

### C. One result, one document, several projections

Phase 2 gave every format a real fact-vs-formatting boundary and a
`ReportDocument` of its own. What it did not establish is the invariant the
definition of done states:

```text
one completed evaluation
    -> one completed semantic report document
        -> JSON / Markdown / HTML / SARIF / JUnit
```

`service_render.render_output()` still dispatches the `DiffResult`,
snapshots, severity configuration, and presentation options down six
independent paths. This is not evidence that today's formats disagree; it
means that wrapping their separately-built projections in the same immutable
type cannot prove they can't. It matters directly to
[ADR-068](068-one-comparison-product-and-scan-retirement.md)'s "one
analysis, several artifacts" experience, which also makes the canonical
report the replacement for the separate scan schema.

**The fix:** finalize compatibility, assurance, scope, dispositions,
consumer impact, and the exit decision *before* format selection, and build
the shared report representation from that completed result. Format-specific
builders may still arrange presentation; they may not be independent
authorities for those decisions. Keep Phase 2's immutable containers and
projection tests and build on them. Coordinate with ADR-063 and ADR-068
rather than starting a second reporting-convergence project — the design is
[`duplication-and-convergence-assessment.md`](../plans/duplication-and-convergence-assessment.md)
Phase 4's `ReportEnvelope`.

**Completion test:** render the same completed document repeatedly and in
different format orders; the semantic content is identical every time, and
no renderer re-runs extraction, policy evaluation, or gate resolution.

### D. Typed request/plan and operand convergence

The shared per-artifact contracts exist (Phase 3), but they carry no
selection, inventory, or acquisition state, so ADR-065's scope model is
still assembled by command-level orchestration and the release fan-out still
has no `ResolvedCompareConfig`-shaped object of its own (`gate.py`'s two
callers fold onto different shapes — see
[ADR-064](064-canonical-gate-algorithm-and-exit-decision.md) and this plan's
own `EffectiveGate`/`EffectiveEvaluationConfig` target). Equivalent CLI and
API input must resolve to equivalent scope, configuration, acquisition
records, and outcomes.

**Completion test:** equivalent CLI and typed-API inputs produce equal
resolved scope, configuration, acquisition records, and outcomes across
live and stored operands; selection, inventory, and acquisition state are
fields on the shared request/plan, not frontend locals.

### E. Storage and model ownership, not file placement

Three unresolved owners, each a value-versus-persistence-versus-
orchestration conflation of the shape Phase 5 already solved for
`*_metadata.py`:

- `serialization.py` has taken real decomposition (platform blocks, several
  `storage/` codecs). **Closed (closure package 5, slice 1):**
  `snapshot_from_dict`'s legacy backfill call to
  `python_ext.detect_python_extension()` — evidence derivation inside the
  loader, which `storage`'s `may_import: [model]` could not admit — now runs
  as an explicit post-load step in `workflows/snapshot_load.py`, reached
  through `serialization.snapshot_from_dict`'s unchanged public signature.
  The sibling `snapshot_platform_blocks.py`'s own `storage -> extract` edge
  (its `_xxx_from_dict` helpers importing dataclasses from the flat parser
  modules) closed the same slice by switching those ~10 imports to each
  dataclass's canonical `model/*_facts.py` home; `snapshot_platform_blocks.py`
  is now classified `storage`. `serialization.py` itself stays unclassified
  (the `public_root_surfaces` compatibility-facade treatment) — its own
  ~1500 remaining lines of codec logic are a separate, not-yet-attempted
  classification.
- `bundle_facts.py` and its serialization/store siblings are classified
  `workflows`, conflating the `BundleFacts` value, its persistence, and
  capture/comparison orchestration. **Not yet started.**
- `probe_harness.py` (`compare`) needed `snapshot_to_dict`/
  `snapshot_from_dict` to serialize its own probe matrix. Comparison logic
  must not become the owner of persistence because a probe workflow needs
  serialized inputs; `compare`'s `may_import: [model]` offers no facade
  route. **Closed (closure package 5, slice 2):** the conversion functions
  (`ProbeResult.to_dict`/`MatrixSnapshot.to_json`/`.from_dict`, and the
  module-level `write_matrix_snapshot`/`load_matrix_snapshot`) moved out of
  `probe_harness.py` entirely into `abicheck/workflows/findings.py` —
  already this ADR's own documented `workflows` re-export surface for "the
  probe matrix" — which legally imports both `compare` (for the
  `ProbeResult`/`MatrixSnapshot` dataclasses) and `abicheck.serialization`
  (the `public_root_surfaces` facade). `ProbeResult`/`MatrixSnapshot` are
  now pure value objects with no `storage` import; `probe_harness.py`
  itself still owns `load_probe_spec`/`run_probe_matrix` (YAML parsing and
  compile orchestration), unchanged. `probe_harness.py` *is* documented
  Python API (`docs/use/probe-harness.md`) — the compatibility question was
  never whether the module is public, only whether the removed
  *serialization* helpers specifically needed a shim at their old path;
  they didn't, since the documented workflow's supported path is that doc
  page, which now imports the moved names from `workflows.findings`
  instead (verified round-tripping) — every internal caller (CLI, tests)
  was switched to the new home in the same slice.

The owners to establish are: `model` for snapshot/bundle value types and
their invariants; `storage` for codecs, schemas, persistence, and schema
migration; `extract` for evidence derivation; `workflows` for capture, load,
enrichment, and comparison orchestration. For legacy loading, decide
explicitly where evidence-derived backfill runs and preserve supported
reader behavior — do not remove or relocate it without auditing every direct
`snapshot_from_dict()` caller. Coordinate with
[ADR-062](062-project-snapshot-storage-v2.md); this is not a competing
storage redesign.

**Completion test:** bundle values, persistence, evidence backfill, and
orchestration each name one owner; legacy-reader behavior is pinned by tests
written before the move (Phase 3's rule); no `compare`-classified module
owns a persistence operation.

### F. A repository-wide completion track

The definition of done requires more than the six phase labels: explicit
ownership for the remaining root modules, legal imports, no responsibility
cycles, shared workflow contracts, pure report projections, bounded facades,
and debt either retired or explicitly accepted. The live ownership map and
`architecture/debt.yaml` still hold substantial legacy implementation and
migration debt.

This is **not** "move every file now" — this ADR rejects mass mechanical
relocation (see "Split every oversized module immediately" under
Alternatives). What it requires is that every remaining legacy area carries
a **disposition**: migrate through a named responsibility slice, retain as a
genuinely supported public module, or accept a specific architectural
exception with a reason. "Unclassified for now" and "see the ownership map"
are not dispositions.

Architectural debt is also tracked independently of line count. A short
module can hold a forbidden dependency; a large, well-owned parser can be a
legitimate reviewed exception. Several ownership questions in this ADR never
got a `debt.yaml` entry only because the file was not oversized.

**Completion test:** every unclassified first-party module under `abicheck/`
has one of the three dispositions recorded; `debt.yaml` holds only accepted
exceptions, described as exceptions rather than as migration work.

## Closure sequence

This replaces the open-ended Phase 4 narrative. Packages 3-5 may run as
bounded parallel slices where they do not compete for the same
result/request contracts. Package 6 must not race ahead of ADR-068: deleting
a `scan` surface before its capabilities and consumers migrate is the
failure that plan's phase ordering exists to prevent.

| Order | Work package | Completion condition |
|---|---|---|
| 1 | Reconcile ADR scope and tracking | Status, acceptance gaps, and the links to ADR-062/063/068 agree; `scan` no longer appears as a future architecture example |
| 2 | Close enforcement escapes and the rendering back-edge (gap A) | A literal `importlib.import_module` call naming a first-party module is visible to `check_architecture.py`'s direction check as a real edge; `workflows/render.py` retires; the remaining `workflows -> frontends`/`frontends -> extract` edges this re-measurement found are real, static, visible, and recorded as reviewed `dependency_direction_exceptions` (not silently dynamic) pending the further migration each names; the public path stays, composed at an outer adapter |
| 3 | Converge completed results and report projections (gap C) | One evaluated result supplies every format; no renderer derives a competing gate, disposition, or assurance decision |
| 4 | Finish typed request/plan and operand convergence (gap D) | Equivalent CLI/API inputs produce equivalent resolved scope, configuration, acquisition records, and outcomes; coordinated with ADR-068's shared-driver work |
| 5 | Close the storage/model splits (gap E) | Bundle values, persistence, evidence backfill, and orchestration have explicit owners; legacy-reader behavior is tested |
| 6 | Finish the facade and legacy retirement (gaps B and F) | Internal callers use canonical owners; unnecessary private shims are gone; `scan`-related surfaces retire only after capability migration; every remaining exception is explicit |

**How closure is validated.** Exercise the architecture's promises through
real public paths, not through internal detectors: equivalent CLI/API
resolution; live and stored operands; selected versus missing members;
stripped-binary and header-only tasks; suppressed findings that remain
visible; consumer impact that leaves the global gate intact; and repeated
multi-format rendering with no re-evaluation. Assert compatibility,
assurance, scope, gate, and process exit **separately** — a passing exit
code is not proof of any of the other four.

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
7. Root `cli.py` and `service.py` are delegation-only facades below 150 lines
   — reached by reducing coupling, and checked last. A facade that hits the
   number by trimming documentation, or that keeps private shims with no
   external contract, does not satisfy this.
8. Every major operation follows `Request -> ResolvedPlan -> Result`, and the
   plan carries selection, inventory, and acquisition state rather than
   leaving them to a frontend.
9. Dry-run renders the actual resolved plan.
10. Every output format consumes **one** immutable `ReportDocument` built
    once per completed evaluation — not one document per format.
11. Extraction cannot import policy, report, workflows, or frontends.
12. Compare cannot decide suppression, severity, or exit status.
13. Renderers cannot alter findings, verdicts, or gate state.
14. Root `AGENTS.md` is a stable routing contract below 350 lines and scoped
    package instructions exist for every responsibility package.
15. A contributor adding an ELF fact, detector, policy rule, CLI flag, or
    report field can identify its owner from one routing table without first
    opening a legacy monolith.
16. `architecture/debt.yaml` is empty or contains only explicitly accepted
    exceptions that are no longer described as migration work, and every
    remaining unclassified first-party module carries one of the three
    dispositions in gap F.
17. No first-party dependency is hidden behind a dynamic import to keep it
    out of the architecture checks (D6); the checks resolve literal
    `importlib.import_module` edges.
18. Every module reachable through `public_root_surfaces` names an owning
    layer, and no canonical internal caller reaches an implementation
    through the compatibility route.
19. Persistence, evidence derivation, value types, and orchestration have
    distinct owners for snapshots and bundle facts alike.

Items 1-16 are the original criteria; 17-19 make explicit the guarantees the
[remaining acceptance gaps](#remaining-acceptance-gaps) found were not
covered by a passing check.

The immediate deliverable after acceptance is Phase 0: establish ownership,
contracts, scoped guidance, and no-growth enforcement. Splitting another
large file before those constraints exist is not progress toward this ADR by
itself.
