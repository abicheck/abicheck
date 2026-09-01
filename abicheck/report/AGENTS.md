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
summary, `render_stat_document`), and `render_xml.py` (JUnit).

`render_xml.py` exists because a `ReportDocument` holds JSON values only —
so a renderer can never be handed a live object graph to mutate — and an
`ElementTree` is not one. `element_to_mapping`/`element_from_mapping` are its
lossless encoding; `render_xml_document` is the projection. Put a report
*fact* (a tag, an attribute, text) in the document and a *formatting* choice
(indentation, the XML declaration) in the projection.

`finding.py` is the per-`Change` counterpart to `document.py`'s whole-report
value: `ReportFinding` holds one change's already-resolved `verdict`/
`category`, built once by `build_report_findings` rather than re-derived at
each renderer's own call site. `report_findings_for` is the memoized
per-`DiffResult` convenience (an attribute cache on the result, not a
`DiffResult` method — `model` may not import this package, so the method
cannot live there); `findings_by_change_id` indexes a set of findings by
`id(change)` for O(1) lookup within one render (never persisted past it —
`Change` has no `__hash__`).

Markdown's richer modes (`to_markdown`, `to_review_digest`) and HTML remain
the one open follow-up slice: they emit prose straight from a `DiffResult`
rather than building a structured value first, so each needs its own rewrite
against its golden output. See ADR-061's Phase 2 status note for the
now-closed halves of items 4 and 5 (per-finding verdict; the fold-ins'
demangle scope) — one piece of item 5 stays open, `cli_compare_fold.py`'s
scoped-gate JSON fold, which needs this package's JSON builders to accept
scoped-gate awareness natively rather than re-deriving already-built
sections post-render.

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
