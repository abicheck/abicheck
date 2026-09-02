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
`render_markdown.py` (Markdown prose), and `render_html.py` (the HTML
report). Every output format now crosses this boundary.

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

`render_markdown.py` and `render_html.py` are the two prose/markup
projections, and they follow one shape you should copy for any new format:
the *source* module (`reporter_markdown.py`, `html_report.py`) keeps a
`compute_*` function per section that reads the `DiffResult`/`Change`
sequence and returns a small frozen struct of plain values — never a
pre-built string or markup fragment — and this package holds the matching
`render_*` that formats it and decides nothing. A section that does not
exist is a `None` from `compute_*`, which is not the same as an empty one.
Filtering that *looks* like formatting stays compute-side whenever it is
really a report decision (which summary rows are non-empty; which reclassify
rules are still active). Both modules keep every pre-split name as a thin
wrapper or re-export, so no caller or existing test changed.

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
