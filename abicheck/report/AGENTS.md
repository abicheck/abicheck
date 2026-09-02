# AGENTS.md — `abicheck/report/`

## Purpose

This package owns immutable report documents, report schemas, grouping, and
pure output projections. A renderer consumes completed workflow facts and
returns text or structured data without changing compatibility decisions.

## Permitted imports

Report code may import model, compare, policy, and workflow result contracts.
It may not import frontend modules or invoke extraction/comparison operations.
Use explicit implementation modules rather than broad compatibility facades.

## Canonical entry points

During ADR-061 migration, each moved projection names its workflow result in
the module name. `aggregate.py` projects `AggregateResult` to JSON-compatible
mapping or text. `document.py` is the shared immutable `ReportDocument`
Phase 2 is establishing across every output format. Its projections are
`render_json.py` (JSON, and therefore SARIF — SARIF is a JSON format and
needs no serializer of its own), `render_text.py` (the one-line `--stat`
summary, `render_stat_document`), `render_xml.py` (JUnit),
`render_markdown.py` (Markdown prose), `render_html.py` (HTML's reusable
per-section formatters/structs), and `render_html_document.py` (HTML's
whole-document projection, `render_html_document`).

**HTML now crosses the single canonical `ReportDocument` boundary; Markdown
does not yet, and that distinction matters when you reach for one as a
model.** JSON, SARIF, JUnit, `--stat`, and now HTML construct a
`ReportDocument` (or, for HTML, `html_report.build_html_document`'s
JSON-shaped mapping wrapped in one) and project it purely; `render_markdown.py`
alone still projects its own per-section frozen structs straight from a
`DiffResult` without an intervening document. Converging Markdown is tracked
in `docs/contribute/plans/duplication-and-convergence-assessment.md`'s
Phase 4. HTML's own closure split the whole-document formatting out of
`render_html.py` into the sibling `render_html_document.py` once the
combined module passed the architecture check's new-file size ceiling —
`render_html.py` keeps the reusable per-section renderers (`ChangeRow`,
`render_changes_table`, `FileMetadataTable`/`render_file_metadata`, and
siblings); `render_html_document.py` owns only "assemble the complete page
from a finished document" (`render_html_document`, its native/compat-mode
halves, and the `_*_from_mapping` reconstruction helpers a `ReportDocument`
round trip requires, since it turns every dataclass into a plain mapping and
every tuple into a list). Note `html_template.render_document` is unrelated
despite the name — it wraps an assembled body in page chrome.

`render_xml.py` exists because a `ReportDocument` holds JSON values only —
so a renderer can never be handed a live object graph to mutate — and an
`ElementTree` is not one. `element_to_mapping`/`element_from_mapping` are its
lossless encoding; `render_xml_document` is the projection. Put a report
*fact* (a tag, an attribute, text) in the document and a *formatting* choice
(indentation, the XML declaration) in the projection.

`finding.py` is the per-`Change` counterpart to `document.py`'s whole-report
value: `ReportFinding` holds one change's already-resolved `verdict`/
`category`, built once per render by `build_report_findings` rather than
re-derived at each renderer's own call site. `report_findings_for` is the
per-`DiffResult` convenience (not a `DiffResult` method — `model` may not
import this package, so the method cannot live there); it recomputes on
every call rather than caching on the result instance, since `DiffResult`
is mutable and a cache there went stale across two renders of the same
result with a mutation in between (Codex review). `findings_by_change_id`
indexes a set of findings by `id(change)` for O(1) lookup within one render
(never persisted past it — `Change` has no `__hash__`).

`render_markdown.py` and `render_html.py`/`render_html_document.py` are the
prose/markup projections, and they follow one shape you should copy for any
new format: the *source* module (`reporter_markdown.py`, `html_report.py`)
keeps a `compute_*` function per section that reads the `DiffResult`/`Change`
sequence and returns a small frozen struct of plain values — never a
pre-built string or markup fragment — and this package holds the matching
`render_*` that formats it and decides nothing. A section that does not
exist is a `None` from `compute_*`, which is not the same as an empty one.
HTML's whole-page assembly (`html_report.build_html_document`) folds every
section's struct into one JSON-shaped mapping before wrapping it as a
`ReportDocument` — the per-section split still holds one level down.

"Decides nothing" includes *registry lookups*, which is the part that is
easy to get wrong because the rendered output is identical either way: a
per-change `category()`/`severity()`/`impact_for()`/`kind_str()` call is a
decision the compute half owes the renderer, not a formatting choice, and
`html_report.py`'s `compute_full_change_rows` -> `ChangeRow` is the shape to
copy for it (a JSON-safe superset of the four registry-lookup fields plus
every raw display field a table row needs — no more `id(change)`-keyed
lookup table, since a `ChangeRow` is itself a plain, hashable-by-value tuple
element). `tests/unit/report/test_render_html.py`'s
`test_render_html_imports_no_decision_making_module` is the structural
guard, parametrized over both HTML render modules: neither may import
`report_classifications`, `checker_policy`, `severity`, `reclassify` or
friends at all. An assertion on rendered strings cannot catch this class —
the import list is what changes.
Filtering that *looks* like formatting stays compute-side whenever it is
really a report decision (which summary rows are non-empty; which reclassify
rules are still active). All three modules keep every pre-split name as a
thin wrapper or re-export, so no caller or existing test changed.

One piece of ADR-061 Phase 2 item 5 stays open: `cli_compare_fold.py`'s
scoped-gate JSON fold, which needs this package's JSON builders to accept
scoped-gate awareness natively rather than re-deriving already-built
sections post-render. See ADR-061's Phase 2 status note for the closed
halves of items 4 and 5 (per-finding verdict; the fold-ins' demangle
scope).

## Tests

Report unit tests migrate to `tests/unit/report/`; stable format contracts stay
in `tests/golden/reports/`. Existing aggregate report assertions remain in
`tests/test_aggregate*.py` until their production projection fully migrates.

## Prohibited responsibilities

A renderer cannot filter findings, change severity, reconstruct a verdict,
calculate a new gate decision, repair a workflow omission, or mutate its
input. It must not read binaries, snapshots, manifests, or CLI configuration.

If a projection needs a fact that the workflow result does not contain, add
the fact at its real owner and carry it through the result contract. Never
infer it from display strings or duplicate the workflow algorithm here.

## Change checklist

Start from an immutable workflow result. Define one projection function with a
fully typed input, preserve ordering and omission rules, and add semantic
assertions in addition to golden output. If the public JSON shape changes,
update its schema owner and version in the same change.

Keep format-specific code independent: JSON, Markdown, HTML, SARIF, and JUnit
must not call one another to recover missing decisions. Shared grouping belongs
in report-owned document construction rather than a renderer.

When moving a legacy renderer, switch internal callers immediately, retain a
facade only for documented imports, update `architecture/debt.yaml`, and prove
the old path delegates without recomputing output.

## Public compatibility

A report schema is a public contract. Additive and incompatible changes follow
the repository's schema-version discipline; a physical module move alone must
remain byte-for-byte compatible. Renderers do not own process exit behavior.

Run focused renderer tests and schema synchronization before the PR profile.
Treat a projection-side policy decision as an ownership regression.
