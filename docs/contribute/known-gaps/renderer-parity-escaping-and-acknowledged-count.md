---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Markdown/review lose symbol characters; three formats omit the acknowledged count

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-28): FIXED.** Found by the renderer-parity matrix, which pinned both defects as strict xfail cells. The cells now pass with their markers removed.

**Escaping.** Markdown (`markdown`, `md`) and the review digest showed a gating symbol containing `<name>` and a backtick as `weird&"*_x`. The raw `<name>` in prose was inline HTML that a CommonMark reader drops, and a backtick closed the code span around the symbol. Only table cells were escaped. Now `abicheck/report/markdown_text.py`'s `md_text` is the one escaping helper, in three shapes: prose (inline-special characters backslash-escaped, so `std::vector<int>` reads back as written), a code span (a backtick fence longer than any backtick run inside the value, with CommonMark padding), and a table cell (pipes escaped, with GFM's backslash pairing). Every Markdown and review emission of a symbol, type, path, version or user-supplied name goes through it: the Breaking Changes list, the review heading and Examples line, the other Markdown change rows, the scope and audit tables, and the release renderers.

The whole-document demangle pass, `abicheck/demangle.py`'s `demangle_text(markdown=...)`, inserts demangled names into finished Markdown. It escapes each one for the context its token sits in (prose or code span), and escapes the bracketed mangled spelling too, so `std::vector<int>` keeps its `<int>` and a boundary underscore in `[_ZN3FooorERKS_]` cannot open emphasis. It also matches the escaped spelling of a mangled token, so a demangled name no longer lands after a dangling backslash. The Markdown escaper is passed in, so the demangler does not import the report package.

Escaping changes the rendered bytes of template-named symbols and of boundary underscores in mangled names. The goldens that changed are `tests/golden/leaked_dependency_symbol.md` and `tests/golden/root_cause/{compatible_addition,func_removed}.md`, regenerated with `--update-goldens`. The only byte change in them is that escape.

**Acknowledged count.** JUnit (the `abicheck.disposition.acknowledged` property), the terminal Audit line and the HTML disposition line now state the acknowledged count. It is `acknowledged_total` from the same disposition audit the JSON report publishes. The one-line summary and Markdown already stated it (ADR-067 D5).

**How it is pinned.** `tests/unit/report/test_renderer_parity_matrix.py` checks every format in the render dispatch table against the JSON report, including the acknowledged count and the gating symbols as a reader sees them. `tests/test_markdown_cell.py` states the escaping contract over generated input as CommonMark read-back properties, and pins the demangled-name path with a fixed demangler, so it needs no `c++filt`. Registered under `report.untrusted_value_escapes_its_cell` and `report.finding_entry_builder_parity` in `tests/regressions/manifest_report.py`.
