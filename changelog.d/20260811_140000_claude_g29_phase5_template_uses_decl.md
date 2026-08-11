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
  A second, independent collision risk (an argument whose decl target
  resolves to nothing this TU's AST indexes, e.g. a C++17 address-of-
  local-static NTTP) is guarded the same way — the whole instantiation is
  dropped rather than falling back to a bare, potentially-colliding
  spelling. A variable-template specialization NTTP target (e.g.
  `H<&ns::v<1>>` for `template <int N> int v = N;`) is also now recognized
  (its own distinct `VarTemplateSpecializationDecl` decl kind, previously
  unindexed the same way an unresolved decl target was) rather than
  colliding with every other instantiation of the same variable template.
  A block-scope `extern` function declaration used as an NTTP target
  (e.g. `void outer() { extern void target(); Holder<&target> h; }`) now
  resolves instead of being incorrectly treated as unresolvable — unlike
  a local variable, a block-scope function declaration always has real
  external linkage. A class-member NTTP target (`H<&A::f>`) still isn't
  resolved (a known, documented gap), but no longer risks colliding two
  distinct member targets sharing a bare name onto one graph node — the
  whole instantiation is dropped instead. A related collision one level
  up is also closed: a **member function template** instantiated once per
  such an unresolvable specialization (e.g. `H<&A::f>::g<int>` vs.
  `H<&B::f>::g<int>`) previously named both `template_decl` scopes
  identically, merging their distinct emitted symbols even though the
  class-level instantiations themselves already correctly stayed
  unmerged — now falls back to a disambiguator unique by construction
  (the specialization's own AST node id) when the specialization's own
  identity can't be resolved. No new `ChangeKind`, no report schema
  change, no verdict/exit-code effect — this is graph vocabulary only.

### Changed

- `ClangTemplateGraphExtractor` moved to the new
  `abicheck/buildsource/template_graph_extractor.py` (a sibling-module
  split, `template_graph.py`'s own 2000-line hard cap) — re-exported from
  `template_graph.py` via a lazy `__getattr__` shim, so
  `from abicheck.buildsource.template_graph import
  ClangTemplateGraphExtractor` keeps working unchanged. New code should
  import it from the new location directly.
