### Fixed

- **`call_graph.py` misclassified virtual/member calls as function-pointer
  calls**: real clang emits a `MemberExpr`'s callee reference
  (`referencedMemberDecl`) as a bare node-id *string*, not the nested dict
  `_find_referenced_decl` only recognized — so `p->f()`-shaped virtual/member
  calls silently fell through to resolve the *receiver*'s own reference
  instead, misclassifying every such call as `call_kind="function_pointer"`
  through `p`. This made `virtual_dispatch_graph.py`'s
  `VIRTUAL_CALL_MAY_DISPATCH_TO` producer effectively inert for the single
  most common virtual-call shape. Fixed with a new `member_index` (clang node
  id -> full decl node, built alongside the existing `id_index`) that
  resolves the string id back to the real declaration, carrying its own
  `virtual`/`type.qualType` fields. Covers `_FUNCTION_DECL_KINDS` nodes
  (`FunctionDecl`/`CXXMethodDecl`/...); a `FieldDecl`-typed callback slot
  invoked via member-call syntax (e.g. `w->cb(x)`) still resolves to no edge
  rather than a wrong one — an improvement, not a full close, and separately
  documented in `callback_graph.py`.
