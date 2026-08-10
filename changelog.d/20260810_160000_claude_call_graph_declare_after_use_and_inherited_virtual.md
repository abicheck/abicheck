### Fixed

- **`call_graph.py` dropped a call to a member declared later in the same
  class body**: `member_index` was built incrementally during the single
  combined AST walk, so a call site reached before its target's own
  declaration (e.g. `struct A { virtual void f(){ g(); } virtual void
  g(); };`) silently resolved to no edge. Fixed with a new whole-AST
  pre-pass (`_index_member_decls`) that indexes every function-decl node
  up front, before any call site is resolved, independent of visit order.
- **`call_graph.py` misclassified a call whose static target is itself an
  override as `direct`/`exact` instead of `virtual`/`overapprox`**: clang
  repeats `"virtual": true` only on a virtual slot's original declaring
  ancestor, never on the override's own declaration — so
  `VIRTUAL_CALL_MAY_DISPATCH_TO` (`virtual_dispatch_graph.py`) missed a
  real further-derived-override chain. Fixed by also recognizing an
  `OverrideAttr`/`FinalAttr` child on the resolved declaration node as a
  virtual signal, kept self-contained within `call_graph.py` (importing
  `override_graph.py`'s own hierarchy-derived virtual-identity set was
  tried first and reverted — it forms a real import cycle the
  AI-readiness `import-cycle-growth` gate correctly rejects).
