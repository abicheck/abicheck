<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **The five reporting-side CodeFactor "Complex Method" findings were
  restructured**, with no behaviour or output changes:
  - `sarif._result_for` (27 → 11) — the properties bag was one long run of
    `if x: properties[...] = x`. The change-detail entries, the
    reachability/graph-impact evidence, and the ADR-049 contract block each
    became their own builder returning a dict the caller merges.
  - `reporter._change_to_dict` (23 → 10) — same shape, same treatment: the
    optional attribution/annotation fields and the reachability/graph-impact
    evidence each became their own builder.
  - `reporter_markdown.to_markdown` (15 → 12) — the `--stat` / `leaf` /
    `root-cause` early dispatch became `_markdown_alternate_rendering`
    (returning `None` for the default view), and the headline summary table
    became `_markdown_headline_table`.
  - `html_report.generate_html_report` (13 → 10) — the CI-gate card, including
    the scoped-vs-full gate split and the blocking-category naming, became
    `_gate_card_html`.
  - `serialization.snapshot_from_dict` — eleven hand-written
    `parser(x) if isinstance(x, dict) else None` ternaries collapse onto one
    shared `_sub_block` helper, which states the forward-compatibility rule (a
    present-but-non-object section is ignored, not fatal) once instead of
    eleven times. This is a deduplication rather than a decomposition: the
    function's branch count was already low (mccabe 12, unchanged) and its
    CodeFactor score comes from operator density, so the score improves
    (radon 66 → 56) without the function being split. Splitting the 530-line
    deserializer into phases is left as its own scoped change.
