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
  `_type_node_kind` helper existed but was never called).
  `ClangTemplateGraphExtractor.extract_from_build`'s cross-TU dedup now
  merges a repeated `(kind, template_qname, label)` instantiation instead of
  keeping whichever TU happened to run first — mirrors `type_graph.py`'s own
  `_merge_type_edges` for the identical richness gap (one TU may resolve an
  argument's `target_qname` or reach an instantiated member another TU's
  translation unit never used). And a dead `out` parameter threaded through
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

  A second review round found three more real gaps, all empirically
  confirmed against real clang AST output before fixing: two distinct
  overloads of the same function template (`f<T>(T)` vs. `f<T>(T,T)`), both
  instantiated with identical template arguments, produce the identical
  *label* (built only from template arguments, never arity/signature) — so
  `template_instantiation_node_id` collapsed both onto one graph node,
  attributing both overloads' emitted symbols to a single instantiation
  identity; it now additionally keys a function-kind instantiation by its
  own unique mangled name, which always differs between overloads even when
  the label doesn't. A class template specialization's own nested member
  function template (e.g. `Holder::apply`) needs the specialization's own
  name added to scope, or two *unrelated* classes sharing a member-template
  name in the same enclosing scope collapse onto one `template_decl` node as
  if they instantiated the same template — confirmed with two sibling class
  templates (`Holder`/`Wrapper`) each declaring their own `apply` member,
  both resolving to the identical bare `api::apply` before this fix. And on
  Darwin, clang's AST reports a mangled name with the platform's extra
  Mach-O leading underscore still attached (`__Z...`), the same
  `call_graph.py`/`type_graph.py` quirk their own `_normalize_mangled`
  already strips — left unstripped here, every `INSTANTIATION_EMITS_SYMBOL`
  edge silently failed to join on Mach-O; this module now carries its own
  independent copy of the same one-line fix.

  A third review round, on `archive_graph.py`: `ar` permits two members
  sharing one name in the same archive (e.g. `ar rc lib.a sub1/util.o
  sub2/util.o`) — `archive_member_node_id` keyed only on `(archive label,
  member name)`, so both members collapsed onto one graph node, and every
  symbol the index attributed to either member attached to that single node
  instead of its own. `ArSymbolRef` now also carries the defining member's
  own `header_offset` (the join key the symbol-index parsers already had in
  hand), and both `archive_member_node_id` and the per-archive member lookup
  key on it, disambiguating without inventing new state. Also:
  `FileReader.__init__` now reads size via `os.fstat` on the already-open
  descriptor instead of a fresh `path.stat()` (which re-resolves the path a
  second time, and whose failure would leak the just-opened handle since a
  raising `__init__` never reaches `__exit__`); `inline.py`'s
  `fold_archive_graph` import moved from eager/top-level into the same
  deferred-import block as the other three fold passes, for one consistent
  import pattern; two stale code comments (`_walk_class_templates` claiming
  recursion it never performs; this fragment's own now-corrected "last
  of"/"first of" wording contradiction against the archive-graph fragment
  covering the same PR) were corrected; and three real-`ar` integration
  tests that guarded only on `ar`'s presence, then unconditionally invoked
  `gcc`, now guard on both (a runner with binutils but no `gcc` would error
  instead of skip).

  A fourth review round found three more real gaps. `cli_buildsource_helpers.
  _collect_source_graph` (the out-of-band `collect --source-abi --source-
  graph summary` path) folded only three of the four clang-backed passes
  `inline._build_inline_graph`'s own `with_call_graph` block runs together,
  and never called `fold_archive_graph` at all — an otherwise-equivalent
  collected pack silently carried no template nodes/edges/coverage stamp,
  and its `static_library` nodes got no archive-member/symbol-definition
  edges regardless of whether `--source-abi` was given; both passes are now
  folded the same way the inline path already does. `source_graph.
  _augment_with_source_abi` minted a `binary_symbol` node only from
  `source_decl_to_binary_symbol` — `source_link.py` accounts for a real
  export under several other mappings too (a template-instantiation export
  matched only through an erased-pattern attribution, a synthesized
  vtable/typeinfo/thunk, an allocator interposer, a genuinely undocumented/
  leaked export), each keyed *by* the symbol; omitting them left no node for
  archive_graph.py's/template_graph.py's own join-only-onto-an-existing-node
  rule to find, so a real archive member defining an undocumented export, or
  a template instantiation's own emitted member matched only through this
  attribution tier, silently went unjoined — all four mappings are now
  seeded. And an *explicit* function-template specialization
  (`template<> int foo<int>(int)`) exhibits the identical detachment quirk
  this module's docstring already documents for class templates — an
  unmangled stub nested under the `FunctionTemplateDecl`, full mangled
  content detached as a top-level sibling sharing the stub's id — but only
  the class-template path had the two-pass id-indexed join needed to
  resolve it; `_walk_function_templates` now has the same join
  (`_collect_full_function_defs`), verified against real clang AST output.
