### Fixed

- **`VIRTUAL_CALL_MAY_DISPATCH_TO` only reached direct, one-hop override
  candidates**: `override_graph.py` records each override against its
  nearest declaring ancestor only, so a multi-level override chain
  (`Base::f <- Mid::f <- Derived::f`) previously left a virtual call
  statically resolved to `Base::f` reaching `Mid::f` but not the equally
  real runtime target `Derived::f`. `virtual_dispatch_graph.py` now walks
  the full transitive closure over `METHOD_POSSIBLE_OVERRIDE`.
- **`TYPE_HAS_VTABLE` missed leaf virtual methods with no override**: seeding
  polymorphism only from `METHOD_POSSIBLE_OVERRIDE` edges left a class with
  a virtual method but no override anywhere in the scanned codebase — a
  common shape — non-polymorphic. `override_graph.py` now also stamps an
  `is_virtual` fact directly on every virtual method's own node
  (`parse_clang_ast_virtual_methods`), independent of any override edge,
  which `TYPE_HAS_VTABLE`'s seeding now reads too.
