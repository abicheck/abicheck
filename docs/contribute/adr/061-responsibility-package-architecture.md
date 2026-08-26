# ADR-061: Responsibility-Package Architecture and Flat-Namespace Migration

**Date:** 2026-08-24
**Status:** Accepted — Phases 0-1 implemented; Phases 2-4 in progress; Phase 5 remains incremental.
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
stat, leaf, and root-cause) now cross that boundary. The `--stat` one-line
text summary (`reporter_markdown.to_stat`) also builds and renders a
`ReportDocument` via `report/render_text.py`'s `render_stat_document`, the
first non-JSON format to do so.

**SARIF and JUnit now cross the boundary too.** `sarif.to_sarif_str` projects
its completed SARIF log through `ReportDocument` + `render_json` — SARIF is
itself a JSON format, so it needs no format-specific serializer. The ADR-050
D2 refusal documents cross the same boundary rather than calling `json.dumps`
on a raw mapping: the JSON one moved to `report/not_comparable.py`, and the
SARIF one renders `sarif.to_sarif_not_comparable`'s existing mapping through
`render_mapping_as_json`. That helper, and its XML counterpart
`render_element_as_xml`, are the one-step "freeze a completed report and
render it" forms; they exist because the three-step spelling reads as
ceremony, which is how a caller talks itself back into `json.dumps` and
silently leaves the boundary — and because the debt-tracked renderers this
phase touches are on a no-growth rule, so a new wrapper belongs in `report/`
rather than in `sarif.py`/`junit_report.py`. JUnit needed one new primitive,
`report/render_xml.py`: a `ReportDocument` stores JSON values only
— deliberately, so a renderer is never handed a live object graph it could
mutate — which an `ElementTree` is not, so `element_to_mapping`/
`element_from_mapping` are its lossless `tag`/`attrib`/`text`/`tail`/
`children` encoding and `render_xml_document` is the projection.  The split
follows the phase's own rule about what is a fact and what is formatting:
the tree's structure and values belong to the document, while indentation and
the XML declaration belong to the projection. All four JUnit entry points
(`to_junit_xml`, `to_junit_xml_multi`, `to_junit_xml_not_comparable`, and the
release fan-out's multi-library suite) route through the one `_to_xml_string`
chokepoint, so all four moved at once, byte-for-byte identically — `ET.indent`
now mutates the rebuilt tree rather than the caller's, which is the observable
half of "a renderer cannot alter its input".

**Not met yet, and this partial status must not be read as the acceptance
criteria having been met.** Two distinct gaps remain, and they are different
sizes:

1. *Markdown's richer modes (`to_markdown`, `to_review_digest`) and HTML.*
   Unlike JSON/SARIF/JUnit, these do not build a structured value and then
   serialize it — they emit prose directly from a `DiffResult` across
   ~3,200 combined lines of helpers that read `Change` attributes one at a
   time. Routing them through a JSON-shaped document is a real rewrite of
   both modules against their golden output, i.e. its own vertical slice
   (plausibly one per format), not a follow-on edit to a serialization
   change.
2. *Items 4 and 5 — decisions and post-render mutation.* Every renderer
   still reaches into policy itself: `sarif._severity_gate_properties`,
   `html_report`'s gate card, and `reporter._build_severity_json` each call
   `severity.compute_gate_decision`, and `junit_report`/`html_report`/
   `reporter_markdown` each resolve a per-finding verdict through
   `effective_verdict_for_change`. These are calls to *the single canonical*
   resolver rather than drifting reimplementations, so the risk today is
   ownership rather than disagreement — but D9 says a projection consumes
   decisions, it does not make them, so closing this needs one
   decisions-computed-once value carried into document construction and read
   by every format. Separately, `cli_compare_fold.py`'s
   `_fold_scoped_compat_into_text`/`_fold_suppression_audit_into_text`/
   `_fold_use_case_impact_into_text` and `cli_compare_helpers.
   _fold_evidence_depth_into_json` are exactly the "post-render mutation"
   item 5 names: they re-parse rendered JSON (or append to rendered
   Markdown) to add facts the workflow result should have carried in the
   first place. Both are behavior-visible changes across every format at
   once, so neither belongs in a slice whose parity argument is
   byte-identical output.

**The `compare -> policy` blocker this section previously recorded is
closed**, and how it was re-measured is worth keeping: the earlier note
claimed classifying `severity.py`/`analysis_assurance.py`/`contract_gating.py`
as `policy` "surfaces the cycle at `checker.py:1277`,
`checker_types.py:36,713,737,749`". Re-running that experiment against the
tree as it actually stood reproduced **one** edge, not five —
`checker_types.py:713`'s function-local `from .severity import
effective_verdict_for_change` — plus the `compare -> policy -> compare`
cycle that single edge closes against `analysis_assurance.py`'s own
(allowed) `policy -> compare` import of `DiffResult`. The other four cited
positions never fired: `checker.py` is not classified at all, so its
`compute_analysis_assurance` call is unchecked either way, and
`checker_types.py`'s `contract_gating` imports are only a violation if
`contract_gating.py` is itself classified `policy`, which the fix below
deliberately declines to do. The lesson is the ordinary one — a blocker
recorded once goes stale as the tree moves, so re-measure before scoping
work against it.

`severity.py` and `analysis_assurance.py` are now classified `policy` in
`architecture/modules.yaml`, with `check_architecture.py` reporting zero
findings. The one real edge was removed by extracting the shared logic to a
leaf both sides may depend on — the same pattern Phase 3's own blocker note
below names: `severity.py`'s `effective_verdict_for_change`, its disclosure
sibling `reclassify_rule_for_change`, and the `KindSets`/`resolve_kind_sets`
pair they share moved into `reclassify.py`, which already owned the
selector-scoped rules that resolver's precedence chain is built around (and
whose two mirrored implementations of that chain had already needed three
separate review rounds to stop disagreeing). `severity.py` re-exports all
three names, so `abicheck.severity.effective_verdict_for_change` and its
siblings keep working unchanged for every caller in and out of this repo;
`DiffResult`'s public `breaking`/`source_breaks`/`compatible`/`risk`
properties are untouched.

Two scope decisions are deliberate rather than oversights. `reclassify.py`
stays **unclassified**: it is the leaf `compare`'s result type and `policy`'s
severity/gating layer both depend on, and which layer finally owns it is
decided by `checker_policy.py`'s own model-vs-policy split, not by this
slice. `contract_gating.py` stays unclassified for the same reason — it is
already documented as a leaf by construction, and `checker_types.py`'s
`_evaluated_changes`/`not_evaluated` depend on it exactly as `severity.py`'s
gate functions do.

What this does **not** resolve: `DiffResult` still exposes policy-resolved
verdict buckets from a `compare`-classified type, which is the underlying
design tension rather than the import edge. Moving those properties off
`DiffResult` outright would be a breaking change to the documented public
Python API (`abicheck/CLAUDE.md`: "Changing their public surface is a
breaking change to the Python API — coordinate it") across ~20 first-party
modules and ~30 test modules, so it is not folded into a reporting slice.
The same applies to `policy_file.py`, which `checker_types.py` imports at
module scope: whichever layer eventually owns it has to answer the same
question.

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

Implementation status: the flat `abicheck/artifact_plan.py` — the
`ResolvedArtifactPlan` cleanup-thunk session type this phase's own target
layout names as `contracts.py` — moved to
`abicheck.workflows.artifact.contracts`, with `abicheck.workflows.artifact`
re-exporting it. The module had zero first-party imports, so this is the
`contracts.py` half of the `Request -> ResolvedPlan -> Result` split with no
behavior change; its four flat call sites (`service_dump_pipeline.py`,
`service_input_resolution.py`, `cli_dump_helpers.py`, `cli_dump_non_elf.py`)
now import it from the new location but are themselves unchanged.

**That blocker is now partly closed, and re-measuring it first changed the
scope** — the same lesson Phase 2 recorded. The note above described a
general coupling; the tree held exactly **four** import edges from the two
service pipelines into `cli_*` modules. But the coupling had leaked much
further than those four edges, because engine-side code that could not
import upward kept private copies instead:

- The depth ladder existed **four** times: `buildsource.scan_levels.
  USER_DEPTHS` plus three independent `_DEPTH_RANK` dicts in
  `cli_dump_helpers.py`, `analysis_assurance.py`, and
  `buildsource/check_report.py`. `analysis_assurance.py` additionally carried
  a hand-copied `evidence_depth_label`, its own comment recording why:
  "duplicated rather than imported ... avoiding a CLI-layer import from this
  leaf-ish module."
- The `abicheck_inputs/` guard `_is_inputs_pack_dir` existed **three** times
  (`cli_buildsource_helpers.py`, `buildsource/l2_seed.py`,
  `cli_dump_dry_run_build_query.py`), each copy's docstring explaining that
  it was a copy and why.

So the blocker was not only an obstacle to moving modules; it was already
being paid for, in duplication, by modules that had no intention of moving.

Two leaves now own that shared logic, exactly as this phase prescribed:

- `abicheck/evidence_depth.py` (classified `model`, the innermost ring, since
  one consumer is `extract`-destined and an extract-to-policy edge would be a
  new inversion) owns `DEPTH_RANK`, `depth_rank`, `weaker_depth`,
  `layer_payload_empty`, `depth_label_for`, `l4_source_abi_was_attempted` and
  `gated_source_label`. `DEPTH_RANK` is *derived* from `USER_DEPTHS`, so the
  ordering has one definition and a new rung cannot leave a copy disagreeing.
- `abicheck/buildsource/pack_shape.py` owns `is_pack_dir` (moved out of the
  oversized `inline.py`, which re-exports it), and
  `buildsource/inputs_pack.py` gained `is_inputs_pack_dir` and
  `is_any_pack_dir`. The pair is split across two modules deliberately:
  putting both in `pack_shape.py` closes `inline -> pack_shape ->
  inputs_pack -> inline`, which `import-cycle-growth` correctly rejects, and
  which is the very cycle the three private copies existed to dodge.

Every prior spelling remains as a delegating alias, so no caller changed.

**Result: `service_dump_pipeline.py` is now free of CLI imports entirely and
is classified `workflows` in `architecture/modules.yaml`** — the first
service pipeline to get a responsibility owner. Three engine-CLI boundary
allowlist entries are gone (15 -> 12); that gate fails on a stale entry, so
the closures are proven rather than asserted. Be precise about what the
classification buys, since the two gates differ: `check_architecture.py`
enforces dependency *direction* against classified layers (a
`workflows -> report` import is rejected, and the resulting cycle reported —
verified by probe), but for a `legacy_paths` module it does **not** flag an
import of an unclassified module, so the CLI boundary is still held by the
separate `engine-cli-boundary` gate (also verified by probe).

**`embed_build_source` has since moved**, closing the edge that note
described. It is now `abicheck/buildsource/embed.py`, and
`service_input_resolution.py` — free of both `click` and `cli_buildsource` —
is classified `workflows` alongside `service_dump_pipeline.py`. Of the eight
helpers the note listed, four were already engine-side in `merge_support.py`;
the rest moved with it (`buildsource/pack_load.py` for the two pack loaders,
`buildsource/snapshot_exports.py` for the export set) or had already moved
(`is_inputs_pack_dir`).

The error contract was the real work, and it is preserved exactly rather than
tidied. Two classes leave the engine and they mean different things to a CI
consumer: `ValidationError` for a malformed `.abicheck.yml` (a *usage* error,
which the CLI renders as `click.UsageError` and `cli.main` remaps to **exit
64**) and `SnapshotError` for an invalid pack (*operational* — the invocation
was well-formed, the data was not, so **exit 1**). Collapsing the two would
tell a CI consumer the invocation was wrong when the data was. The typed
surface still flattens both onto `SnapshotError`, because that is what its
callers have always had to catch; widening it would have been a breaking API
change made in passing.

Seven characterization tests (`tests/test_build_source_embed_errors.py`) were
written and committed *before* the move and pass unchanged after it, pinning
both exit codes at the CLI and both error classes at the function boundary.
Every code was measured against the real CLI, not read off the source. Two
things that measurement corrected: the typed bad-config path is unreachable
through `CompareRequest` (`InputSpec` has no `build_config` field) and is
reachable only via `embed_side_build_source`'s own keyword; and the
`cannot parse build config <path>:` prefix is added *above*
`embed_build_source`, so the two boundaries produce different strings.

**That last edge is closed, and the phase's target layout is now real.**
`prepare_embedded_build_source`/`attach_evidence_metrics`/
`diff_embedded_build_source` moved to `buildsource/evidence_report.py`, so
`service_compare_pipeline.py` is the third and final service module with a
`workflows` owner. The engine module owns no output stream: it renders the
ADR-028 D7 coverage/capability report as lines and hands them to an optional
`on_output` sink, replacing a `quiet` flag that was only ever meaningful to a
caller that had a stream — `run_compare_request` never did, and had to pass
`quiet=True` forever to suppress writes to a stream it does not own. The error
contract is preserved rather than tidied: a malformed pack stays
`SnapshotError` → exit **1** (operational), never the 64 a usage error gets,
pinned by `tests/test_evidence_report_contract.py`, written before the move.

`service_input_resolution.py` then split along the seam it already had:
`workflows/artifact/resolve.py` decides a plan without running it,
`workflows/artifact/execute.py` runs one and reports what it achieved. Three
of this phase's acceptance criteria are structural properties of that split
rather than claims about it — extraction happens once per artifact because
`resolve_side_snapshot` is the single entry point every front end reaches;
resource lifetimes cover execution because the L2 seed's cleanups drain
between the header parse and the embed; achieved depth is a result fact on
`SideResolution`, not a frontend guess. The old module remains a delegating
facade.

The sharpest criterion — "dry-run renders the same resolved plan normal
execution consumes" — needed one more change to be true rather than nearly
true. `--dry-run` already rendered a real `ResolvedDumpRequest`, but the real
run consumed `dump_cmd`'s own locals, which merely *agreed* with it under a
parity test. `dump_cmd` now resolves the request once, above the branch, and
both paths read every field the plan owns off it. Ruff flagging the old locals
as unused is the evidence there is now one derivation rather than two.
Hoisting the resolve also fixed a real inconsistency it exposed: a bare `dump`
and a bare `dump --dry-run` rejected the *same* invalid input with two
different messages.

**Still open:** the real ELF/PE/Mach-O run still *executes* through
`perform_elf_dump`/`handle_non_elf_dump` rather than `execute_dump_request`.
What changed is which object supplies its resolved inputs. That last migration
needs the ADR-039 collector's CLI-only inputs represented in the typed API and
`_write_snapshot_output`'s provenance/`--inputs`/depth-gate sequence reordered
around a resolve-time embed; it is also unverifiable here, since the default
header backend is castxml and no policy-conformant build is obtainable in this
environment (a hand-assembled conda-forge 0.6.13 segfaults in `ParseAST`).

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

Implementation status: the `abicheck.frontends` package now exists, with its
first tenant — `frontends.cli.options.secondary_output` (moved from the flat
`abicheck/cli_secondary_output.py`) — covering the `--write FORMAT=PATH`
option factory and its coherence validator. It qualified for an immediate
move for the same reason `artifact_plan.py` did for Phase 3: zero
first-party imports, so a physical relocation changes no import-cycle or
dependency-direction fact elsewhere. Its four call sites
(`cli_options.py`, `cli_scan_helpers.py`, `cli_compare_helpers.py`,
`cli_compare_release.py`) now import it from the new package.

**The option-cluster half of the blocker below did not survive
re-measurement** — the third time in this ADR a recorded blocker has turned
out to describe a tree that had since moved. The note said the cluster's
"~3,800 combined lines import each other". They do not: an AST scan of the
six modules found a **star, not a tangle**. Five of them (`cli_params`,
`cli_profiles`, `cli_options_contract`, `cli_contract_options`, `cli_help`)
have **zero** intra-cluster imports; only the `cli_options.py` hub imports
them. Four of the five have zero first-party imports of any kind, which is
exactly the criterion `secondary_output.py` was moved on.

Those four moved — 1,249 lines, six call sites:

| was | now |
|---|---|
| `cli_profiles.py` | `frontends/cli/options/profiles.py` |
| `cli_contract_options.py` | `frontends/cli/options/contract.py` |
| `cli_options_contract.py` | `frontends/cli/options/inventory.py` |
| `cli_help.py` | `frontends/cli/help.py` |

The two renames are deliberate. `cli_options_contract` (the `cli-contract`
gate's flag inventory and budget ledger) and `cli_contract_options`
(ADR-049's contract-evaluation options) are unrelated things whose names
differed only by word order — precisely the "physical ownership is
ambiguous" problem in this ADR's own Context. `inventory.py` and
`contract.py` say which is which.

**`cli.py` is now a registration facade: 1959 lines to 128.** Everything else
moved into this package — `frontends/cli/commands/dump.py`,
`frontends/cli/commands/compare.py`, `frontends/cli/runtime.py` (verbosity,
output, provenance, the exit decision) and `frontends/cli/moved.py` (the
historical import surface). What remains in `cli.py` is the Click root group,
its `--version`/SIGTERM wiring, the tail-of-module registration imports, and a
lazy `__getattr__` that keeps every historical `abicheck.cli` spelling
resolvable through `importlib.import_module` at *access* time — a runtime
call, not a static import edge, so the facade never grows a top-level
dependency on the packages that import back into it.

Getting there meant classifying the whole `cli_*` family as `frontends`, which
surfaced **47 real direction violations**: the CLI reaching past the engine
into `policy`, `compare` and `extract`. Those were closed, not suppressed. Each
now routes through a `workflows` re-export surface, and the largest of them is
this phase's own item 4 made executable — `workflows/gate.py` is the one place
a frontend gets its process response, because three orthogonal axes feed one
exit code (verdict, ADR-049 contract-coverage floor, assurance floor) and a
frontend importing them separately is free to fold two and forget the third.
`workflows/extraction.py`, `findings.py` and `scan_config.py` cover the rest;
`scan_config.py` is an owner rather than an alias, holding the three functions
`service_scan` previously had to import *upward* for. Those were the last
`ENGINE_CLI_BOUNDARY_ALLOWLIST` entries of their kind: 15 → 4 across Phases 3
and 4.

Two consequences are worth recording because they are not obvious. A
`monkeypatch.setattr` against a name resolved through `cli.__getattr__`
rebinds nothing the real caller reads, and a re-export surface **binds** its
names at import time, so patching the original module afterwards does not
reach a caller coming through the facade. Both are ordinary Python semantics
rather than anything this design invents, but the indirection makes them easy
to miss; the test suite was repointed accordingly, and doing so found one
patch target (`abicheck.cli._detect_binary_format`) that had been inert
*before* this phase — `_normalize_binary_input` always resolved it through
`cli_resolve`'s own global.

`abicheck.cli` is deliberately **not** added to `architecture/modules.yaml`'s
`facades` list. That gate's `facade` rule means something narrower than this
phase's prose does — only imports, inert assignments and a `TYPE_CHECKING`
block — so any module declaring a Click root group fails it, `main` being a
`FunctionDef`. Widening it to admit this file would weaken it for the pure
re-export modules it was written for, so the 150-line budget is pinned in
`tests/test_cli_moved_surface.py` instead.

`cli_params.py` (452 lines) stays flat: it imports four unclassified flat
modules (`policies`, `policy_file`, `suppression`,
`buildsource.scan_levels`), and `frontends/` is a *migrated* package, so a
module physically inside it is subject to `unclassified-import`. It moves once
those owners exist.

**Still open: `service.py` (1763 lines).** Its
`resolve_input`/`_run_dump_uncached`/`compare_snapshots` (hundreds of lines
each) *are* the current dump/compare implementation, not adapters over an
already-existing workflow object this phase could point them at instead —
moving that logic into `workflows/` is Phase 3's own item 2. Phase 3 has now
given all three service pipelines `workflows` owners and completed the
per-artifact `resolve`/`execute` split, so the destination finally exists; what
does not exist yet is a `workflows` home for `service.py`'s own three large
functions. Thinning it before that would mean either a wrapper around the same
inline logic (achieving nothing toward the acceptance criteria) or a second,
duplicate implementation with nothing shared to delegate to.

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
