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
Phase 2 established across every output format. Its projections are
`render_json.py` (JSON, and therefore SARIF — SARIF is a JSON format and
needs no serializer of its own), `render_text.py` (the one-line `--stat`
summary, `render_stat_document`), `render_xml.py` (JUnit),
`render_markdown.py` (Markdown prose), `render_html.py`/`render_html_document.py`
(HTML's per-section formatters/whole-document projection),
`render_markdown_document.py` (Markdown's `to_review_digest`/`to_markdown`
default view), and `render_markdown_alternate.py` (Markdown's `--report-mode leaf`/`root-cause`). `scoped_gate.py` (below) mutates a JSON `dict`, not a result.

**Every format now crosses the canonical `ReportDocument` boundary — ADR-061
Phase 2's Markdown item is closed in full.** JSON, SARIF, JUnit, `--stat`,
HTML, and every Markdown view (default, review digest, leaf, root-cause)
construct a `ReportDocument` and project it purely; `reporter_markdown.py`
builds no report section directly any more — `_to_markdown_leaf`/
`_to_markdown_root_cause` are thin `build_*`/`render_*` delegates, same
shape as `to_markdown`'s full-mode branch. Markdown's per-`Change` sections
need a JSON-safe row `Change` isn't, mirroring HTML's `ChangeRow`:
`render_markdown_document.py`'s `_change_row`/`_row_contract_tag` family
resolves `impact_for` compute-side and is shared (imported, not duplicated)
by `render_markdown_alternate.py`'s leaf-mode row renderer, leaving
`render_markdown.py`'s `_format_change_md` family serving only its one
remaining caller, the scoped-gate text append in `cli_compare_fold.py`.
HTML's and Markdown's whole-document assembly each split into its own
`render_*_document.py` sibling once the combined module passed the
architecture check's new-file ceiling — Markdown split again once
leaf/root-cause landed. `render_html.py`/`render_markdown.py` keep the
reusable per-section renderers; the `*_document.py`/`*_alternate.py`
siblings assemble the complete report from a finished document plus the
`_*_from_mapping` reconstruction helpers a round trip requires (dataclass ->
mapping, tuple -> list). `html_template.render_document` is unrelated.

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
-> render pass. It and both `render_markdown_*.py` document modules reach
legacy, still-flat `reporter`/`reporter_markdown` via `importlib`, never a
static import (`modules.yaml`'s `frozen_root_families` forbids a new
`abicheck/reporter_*.py` sibling, and a static import would close a real
import-cycle-growth violation).

## Tests

Report unit tests migrate to `tests/unit/report/`; stable format contracts
stay in `tests/golden/reports/`. Existing aggregate report assertions remain
in `tests/test_aggregate*.py` until their production projection migrates.

## Product-facing report invariants

Local consequences of root `AGENTS.md`'s "Product decisions and change
routing" section (the vision's reporting rules). Each names the fact the
document must *carry*; a view that lacks the fact is a document gap to fix
at the owner, never something a renderer infers:

- **Compatible additions are visible changes.** A compatible run still
  itemizes what was added; "0 breaking" is not "nothing happened".
- **Raw versus effective totals.** Every view — compact, review digest,
  one-line, PR comment included — carries the detected total, the
  effective (gating) total, and the per-disposition counts with rule
  provenance. Collapsing detail is fine; dropping these counts is not.
- **Qualified uncertainty.** Unavailable, unsupported, not applicable, not
  requested, and failed evidence render as distinct states; a disabled
  detector reads as *not evaluated*, never as zero findings.
- **Global versus consumer results.** A known consumer's impact enriches
  the report next to the global contract status; it never replaces or
  overwrites it, and one raw change is counted once however many consumers
  it affects.
- **Scope and selection are stated.** Which members/variants were selected,
  which were out of scope, which were expected but missing, and why.
- **Rendering never changes a gate.** Report profiles, modes, and
  `--show-only` reorder or hide detail; they cannot alter a verdict,
  disposition, exit code, or coverage contribution (already enforced by the
  import guards above — keep it that way for any new view).

Where the current renderers fall short (e.g. the one-line format carries
no suppression totals today), the gap is recorded in
`docs/contribute/plans/vision-api-abi-evolution.md`, not silently patched
renderer-side.

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
