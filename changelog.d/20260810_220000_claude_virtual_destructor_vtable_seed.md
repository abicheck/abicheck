### Fixed

- **A class whose only virtual member is its destructor was invisible to
  `virtual_dispatch_graph.py`'s vtable-presence detection**: destructors are
  deliberately excluded from override-edge matching (the Itanium ABI's dual
  `D1`/`D2` mangling needs its own verified rule, not reused from ordinary
  methods), and a bare, uncalled destructor typically has no `decl://` node
  for the leaf-virtual-method seed to read either. A new, narrower
  `override_graph.parse_clang_ast_virtual_destructor_owners` pass — reading
  just the `virtual: true` flag clang already stamps directly on a
  destructor's own AST node, no override-pair matching involved — closes
  this with a third, independent vtable-presence seed.
