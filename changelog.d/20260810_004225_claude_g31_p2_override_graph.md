### Added

- **New L5 source-graph edge kind, `METHOD_POSSIBLE_OVERRIDE`**
  (ADR-041 P2 item 1, `abicheck/buildsource/override_graph.py`): closes the
  loop the call graph's `CALL_KIND_VIRTUAL`/`RESOLUTION_OVERAPPROX` opened —
  which declarations are the actual override candidates for a virtual
  dispatch slot. Built from a Clang AST's class hierarchy
  (`type_graph.py`'s own resolved `TYPE_INHERITS` edges, reused rather than
  re-derived) plus each class's own methods, matched against the NEAREST
  ancestor that actually declares a same-signature method — walking past an
  intermediate class that doesn't redeclare it at all (e.g. `Base` declares
  a virtual `run()`, `Mid : Base` doesn't redeclare it, `Derived : Mid`
  overrides it — the real target is `Base::run`), with the signature match
  itself (`(name, type.qualType)`) normalizing away the exception
  specification first, since an override may legally STRENGTHEN it (e.g.
  adding `noexcept` to an unmarked base virtual) while remaining a real
  override — both verified against real Clang 17 output (Codex review,
  fresh evidence). An edge carries `override_confirmed` (the overriding
  declaration wrote the `override` keyword — a compiler-checked signal) or
  the weaker `override_signature_match` (no keyword, matched purely by
  signature) in its `resolution` attribute. Multiple inheritance emits an
  edge to each matching ancestor; only `CXXMethodDecl` participates in this
  first slice (constructors/destructors and class-template specializations
  are deliberately out of scope — see the module's own docstring); a
  covariant return type is a documented false negative, not a false
  positive. Folded automatically alongside the existing call/type-graph
  passes whenever `dump --sources`/`--build-info` builds the L5 graph with
  Clang available (`inline_graph_fold.fold_semantic_graphs`) —
  best-effort, degrading gracefully (no edges, an extractor row recorded)
  when `clang++` is unavailable, never aborting collection.
