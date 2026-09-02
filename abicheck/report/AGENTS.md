# AGENTS.md — `abicheck/report/`

## Purpose

This package owns immutable report documents, report schemas, grouping, and
pure output projections. A renderer consumes completed workflow facts and
returns text/structured data without changing compatibility decisions.

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
per-section formatters), `render_html_document.py` (HTML's whole-document
projection), and `render_markdown_document.py` (Markdown's: `to_review_digest`
and `to_markdown`'s default view). `scoped_gate.py` (below) mutates a JSON
`dict`, not a result.

**HTML and Markdown's full mode + review digest now cross the canonical
`ReportDocument` boundary; Markdown's leaf/root-cause modes and `--stat`'s
adjacent paths do not yet.** JSON, SARIF, JUnit, `--stat`, HTML, and
Markdown's default view construct a `ReportDocument` (HTML via
`html_report.build_html_document`; Markdown via `render_markdown_document.
build_markdown_document`/`build_review_digest_document`) and project it
purely; `reporter_markdown.py`'s leaf/root-cause modes alone still build
their own per-section structs straight from a `DiffResult`, no document --
tracked in `duplication-and-convergence-assessment.md`'s Phase 4. Markdown's
per-`Change` sections needed a JSON-safe row `Change` itself isn't, mirroring
HTML's `ChangeRow`: `render_markdown_document.py`'s own `_change_row`
family resolves `impact_for` compute-side, leaving `render_markdown.py`'s
`_format_change_md` family serving leaf/root-cause mode unconverted. HTML's and Markdown's
whole-document assembly each split into its own `render_*_document.py`
sibling (once each combined module passed the architecture check's
new-file ceiling): `render_html.py`/`render_markdown.py` keep the reusable
per-section renderers; `render_html_document.py`/`render_markdown_document.py`
own only "assemble the complete page/report from a finished document" plus
the `_*_from_mapping` reconstruction helpers a document round trip requires
(it turns every dataclass into a plain mapping, tuple into a list).
`html_template.render_document` is unrelated despite the name — page chrome.

`render_xml.py` exists because a `ReportDocument` holds JSON values only, and
an `ElementTree` is not one. `element_to_mapping`/`element_from_mapping` are
its lossless encoding; `render_xml_document` is the projection. Put a report
*fact* in the document, a *formatting* choice (indentation, XML declaration)
in the projection. `finding.py` is the per-`Change` counterpart to `document.py`'s whole-report
value: `ReportFinding` holds one change's already-resolved `verdict`/
`category`, built once per render by `build_report_findings` rather than
re-derived at each renderer's own call site. `report_findings_for` is the
per-`DiffResult` convenience (not a `DiffResult` method — `model` may not
import this package); it recomputes every call rather than caching on the
result instance, since a mutable `DiffResult` cache went stale across two
renders with a mutation between them (Codex review). `findings_by_change_id`
indexes findings by `id(change)` for O(1) lookup within one render only
(`Change` has no `__hash__`).

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
`html_report.py`'s `compute_full_change_rows` -> `ChangeRow` (and Markdown's
`render_markdown_document._change_row`) is the shape to copy: a JSON-safe
row carrying every registry-lookup result plus raw display field a table
row needs. `tests/unit/report/test_render_html.py`'s
`test_render_html_imports_no_decision_making_module` is HTML's structural
guard (parametrized over both HTML render modules: neither may import
`report_classifications`, `checker_policy`, `severity`, `reclassify` or
friends) — an assertion on rendered strings cannot catch this class, the
import list is what changes. Filtering that *looks* like formatting
stays compute-side when it is really a report decision (which summary rows
are non-empty; which reclassify rules are still active). Every module keeps
a pre-split name anything still resolves through — a re-export avoids
breaking a real caller, not to freeze a name forever (D8: "not retained
solely because a test monkeypatches it... the test moves with the
implementation"). HTML's and Markdown's whole-document closures each
retired every name left with zero resolvers repo-wide, tests included,
once every caller moved onto the new `build_*_document`/`ChangeRow`-shaped
path (see git history for the exact lists); a name with a real surviving
caller (e.g. `_abbr_symbol_text`/`_changes_table`, `appcompat_html.py`'s)
stays.

ADR-061 Phase 2 item 5 is closed: `scoped_gate.py`'s `apply_scoped_gate`
folds scoped-gate JSON natively (pre-render), not a render -> parse -> patch
-> render pass. It and `render_markdown_document.py` reach legacy, still-flat
`reporter`/`reporter_markdown` via `importlib`, never a static import (which
would close a real import-cycle-growth violation), since `modules.yaml`'s
`frozen_root_families` forbids a new `abicheck/reporter_*.py` sibling.

## Tests

Report unit tests migrate to `tests/unit/report/`; stable format contracts
stay in `tests/golden/reports/`. Existing aggregate report assertions remain
in `tests/test_aggregate*.py` until their production projection migrates.

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
facade only for documented imports, update `architecture/debt.yaml`, and
prove the old path delegates without recomputing output.

## Public compatibility

A report schema is a public contract. Additive and incompatible changes follow
the repository's schema-version discipline; a physical module move alone must
remain byte-for-byte compatible. Renderers do not own process exit behavior.

Run focused renderer tests and schema synchronization before the PR profile.
Treat a projection-side policy decision as an ownership regression.
