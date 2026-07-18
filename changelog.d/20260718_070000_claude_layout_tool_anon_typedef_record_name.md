<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- `abicheck-clang-layout-tool` emitted the empty `getQualifiedNameAsString()`
  spelling for an anonymous record — the common C idiom
  `typedef struct { ... } Foo;` — so `apply_layout_facts` (which matches
  purely by name) could never find a layout entry for it, silently losing
  size/offset enrichment for every typedef'd anonymous C struct/union even
  though the tool computed the facts. Now resolves the record's display
  name through Clang's `TagDecl::getTypedefNameForAnonDecl()` when the tag
  itself is anonymous, mirroring `dumper_clang.py`'s own
  anonymous-record-under-typedef-name handling.
