### Fixed

- **Two uninstantiated function/method templates differing only by
  template-parameter *kind* no longer collapse onto one `EntityId`.**
  `template<class T> void f();` and `template<int N> void f();` share
  scope, leaf name, and an identical (empty) ordinary parameter list, and
  neither gets a real mangled name from clang's header-AST backend
  (uninstantiated templates aren't mangled) — so `entity_id_for_function`'s
  `"sig"` signature-fallback tuple, built only from ordinary parameter
  types/qualifiers, had nothing left to distinguish them by. Fixed by
  extracting each `FunctionTemplateDecl`'s own per-position
  parameter-kind signature and folding it into the fallback tuple.
