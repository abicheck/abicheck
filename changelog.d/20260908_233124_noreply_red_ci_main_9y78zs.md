### Fixed

- **A single-underscore Darwin `asm("_foo")` label on the `--ast-frontend
  clang` backend no longer collapses to the wrong, signature-free entity
  identity.** A prior fix kept such a label's mangled name preserved
  (`"_foo"`, not stripped to `"foo"`), but `is_extern_c`'s Darwin-gated
  `symbol_candidates` fallback still misclassified the declaration as
  extern "C" — a single-underscore explicit label is exactly as
  candidate-matchable as genuine Darwin linker decoration. The resulting
  `EntityId` still took the wrong, signature-free `("extern_c",)` branch
  instead of `("mangled", "_foo")`, diverging from castxml's own
  mangled-name identity for the identical declaration and risking a
  manufactured remove/add pair in a castxml/clang or hybrid comparison.
  `is_extern_c`'s `symbol_candidates` branch is now also gated
  `and not has_explicit_asm_label(node)`, in both
  `extract.headers.clang.functions.parse_functions` and
  `dumper_clang.parse_variables`.
