### Added

- **A public template instantiation's own type arguments and emitted symbols
  are now graph facts.** `abicheck/buildsource/template_graph.py` is a
  third, independent `clang -ast-dump=json` pass (alongside the existing
  call and type graph passes), closing the "public template → concrete
  instantiation → internal specialization → emitted exported symbol" chain
  the pre-existing passes can't: they only ever see a template's *pattern*,
  never a specific instantiation's arguments. New `template_decl`/
  `template_instantiation` graph nodes and `DECL_INSTANTIATES_TEMPLATE`/
  `TEMPLATE_USES_TYPE`/`INSTANTIATION_EMITS_SYMBOL` edges, driven by
  `inline_graph_fold.fold_template_graph` whenever the call/type graph
  passes run. A resolved template argument (e.g. `Wrapper<internal::Detail>`)
  joins onto the same `record_type`/`enum_type` node `type_graph.py` would
  use for the identical qualified name, via clang's own `decl` cross-
  reference on the `TemplateArgument` node — exact identification, not a
  textual heuristic, and it resolves straight through a `using`/typedef
  alias argument to the real declaration. A class instantiation's own
  instantiated member functions' mangled names join onto an existing
  `binary_symbol` node only (ADR-057 D1's join-by-shared-node-id rule,
  reapplied) — an instantiated-but-never-exported member mints no node.
  `TEMPLATE_USES_DECL`/`INSTANTIATION_MAPS_TO_EXPORT`/
  `DECL_USES_DEFAULT_TEMPLATE_ARG`/`CONSTRAINT_DEPENDS_ON_DECL` remain
  reserved, unpopulated vocabulary — see the module's own docstring for why
  each is deferred. This closes the first of G29 Phase 5's five named open
  graph families (object/archive link provenance was the other closed one);
  virtual dispatch, macro/config, and callback/function-pointer remain
  open. No new `ChangeKind`, no report schema change, no verdict/exit-code
  effect.

### Fixed

- `source_graph.NODE_KINDS`/`EDGE_KINDS`'s object/link-provenance vocabulary
  (`object_file`/`archive_member`/`static_library`/…,
  `ARCHIVE_CONTAINS_OBJECT`/`OBJECT_DEFINES_SYMBOL`/…) moved out of
  `source_graph.py`'s own inline declarations into
  `graph_facts.LINK_PROVENANCE_NODE_KINDS`/`LINK_PROVENANCE_EDGE_KINDS`,
  unioned in the same way the consumer/use-case/template vocabulary already
  is — a pure relocation (no behavior change) needed to keep
  `source_graph.py` under its 2000-line hard cap while adding the template
  vocabulary above.

  Four more fixes from a review round on `template_graph.py` itself: a
  `ClassTemplateSpecializationDecl` used as a *nested* template argument
  (`Outer<Wrapper<int>>` vs. `Outer<Wrapper<double>>`) resolved its argument's
  `target_qname` to the identical bare, unparameterized primary-template name
  for both — empirically confirmed against real clang AST output — so the
  two collided onto one graph node instead of staying distinct; the resolver
  now recurses through the target's own template arguments the same way the
  top-level per-instantiation walk already disambiguates itself. A resolved
  argument's `TemplateArgUse` now also carries the target's own raw clang
  decl kind, so `augment_graph_with_templates` mints the correct `enum_type`/
  `typedef`/`record_type` graph node for it instead of defaulting every
  resolved argument to `record_type` regardless of what it actually is (the
  `_type_node_kind` helper existed but was never called). `ClangTemplateGraph
  Extractor.extract_from_build`'s cross-TU dedup now merges a repeated
  `(kind, template_qname, label)` instantiation instead of keeping whichever
  TU happened to run first — mirrors `type_graph.py`'s own `_merge_type_edges`
  for the identical richness gap (one TU may resolve an argument's
  `target_qname` or reach an instantiated member another TU's translation
  unit never used). And a dead `out` parameter threaded through
  `_walk_class_templates` (never appended to — the function only ever
  *registers* class-template membership) was removed, along with a
  `_MEMBER_FUNCTION_KINDS`-equivalent tuple duplicated inline in
  `_walk_function_templates`.

  Separately, a real macOS CI failure surfaced a pre-existing test gap
  (unrelated to any code path this feature touches): `test_archive_graph.py`'s
  real-`ar` round-trip used an uninitialized global (`int gamma_sym;`) to
  exercise a data-symbol export — a tentative definition becomes a COMMON
  symbol, and whether a platform's `ranlib`/`ar` indexes a COMMON symbol the
  same way as an ordinary defined one is toolchain-specific (macOS's system
  `ar` omits it). The test now initializes the value, sidestepping the
  ambiguity rather than asserting a platform-dependent fact the test was
  never actually trying to verify.
