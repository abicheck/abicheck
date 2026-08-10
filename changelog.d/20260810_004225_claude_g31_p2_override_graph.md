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
  edge to each matching ancestor whose OWN slot is independently virtual —
  a same-signature but non-virtual method on a sibling base is a real,
  legal C++ shape (`override` only requires overriding at least ONE base
  virtual, and clang accepts it) that must not be recorded as a second,
  false override target (Codex review, fresh evidence, verified against
  real Clang 17 output); only `CXXMethodDecl` participates in this
  first slice (constructors/destructors and class-template specializations
  are deliberately out of scope — see the module's own docstring); a
  covariant return type is a documented false negative, not a false
  positive, and so is a base method spelling a parameter/return type
  through a typedef that the override spells as the underlying type
  directly (`type.qualType` differs even though clang accepts the
  override) — both verified against real Clang 17 output and pinned with
  regression tests rather than guessed at (Codex review, fresh evidence).
  A mangled-name identity is normalized the same way the call/type graph
  already does (`type_graph._normalize_mangled`, reused directly) — on
  macOS clang reports a Mach-O ABI leading underscore (`__ZN...`) that,
  left unstripped, would leave an override edge's endpoint a disconnected
  duplicate of the SAME method's call/type-graph node instead of merging
  onto it (Codex review, fresh evidence, verified against real
  `clang++ --target=x86_64-apple-darwin` output). Folded automatically
  alongside the existing call/type-graph passes whenever
  `dump --sources`/`--build-info` builds the L5 graph with Clang available
  (`inline_graph_fold.fold_semantic_graphs`) — best-effort, degrading
  gracefully (no edges, an extractor row recorded) when `clang++` is
  unavailable, never aborting collection.
