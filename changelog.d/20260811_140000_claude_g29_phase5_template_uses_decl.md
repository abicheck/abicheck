### Added

- **`TEMPLATE_USES_DECL` (G29 Phase 5 item 1 follow-up)** — the template
  graph now populates the one edge kind left reserved when template
  instantiation extraction first landed: a pointer-to-function/variable or
  reference non-type template argument (e.g. `Callback<&ns::handler>`)
  gets a `TEMPLATE_USES_DECL` edge onto the same `source_decl` node
  `call_graph.py`/`type_graph.py` would themselves mint for the identical
  declaration, closing a real gap where such an argument's whole
  instantiation was previously silently dropped from the graph entirely
  (the same "opaque template-template argument" guard that correctly
  drops an unspellable argument was also — incorrectly — catching this
  spellable one). New `abicheck/buildsource/template_graph_value_decls.py`
  (`index_value_decls`/`arg_label_spelling`) builds the id → identity
  index this needs and closes a label-collision risk a prior investigation
  round had flagged and left unimplemented (two distinct instantiations
  differing only by which namespace's same-named callee they reference —
  `Holder<&ns1::f>` vs. `Holder<&ns2::f>` — no longer collide onto one
  graph node). Deliberately scoped to free functions and namespace-scope
  variables only; a class-member NTTP target is a known, left-open gap.
  No new `ChangeKind`, no report schema change, no verdict/exit-code
  effect — this is graph vocabulary only.

### Changed

- `ClangTemplateGraphExtractor` moved to the new
  `abicheck/buildsource/template_graph_extractor.py` (a sibling-module
  split, `template_graph.py`'s own 2000-line hard cap) — re-exported from
  `template_graph.py` via a lazy `__getattr__` shim, so
  `from abicheck.buildsource.template_graph import
  ClangTemplateGraphExtractor` keeps working unchanged. New code should
  import it from the new location directly.
