### Fixed

- **A genuinely plain-C Darwin `asm("_foo")`-labeled declaration no longer
  gets misclassified as a deliberate C++ identity override.** A prior fix
  excluded every asm-labeled declaration from `is_extern_c`'s Darwin
  `symbol_candidates` fallback, but real Clang emits the identical AST
  shape (same literal `mangledName`, same `AsmLabelAttr` child, no
  `LinkageSpecDecl`) whether `void foo(void) asm("_foo");` is compiled as
  C or C++ — there is no per-declaration signal to tell them apart. C has
  no mangling to override in the first place, so treating a routine
  glibc/POSIX-style plain-C asm label as a deliberate identity override
  broke self-comparison of an unchanged plain-C header, reporting a
  spurious `FUNC_LANGUAGE_LINKAGE_CHANGED`. The clang backend's
  `_ClangAstParser` now threads the caller's own resolved compile-language
  mode (`dumper._clang_header_dump`'s `resolved_force_cpp`) down as
  `is_cxx`, and the exclusion is gated `and not (has_asm_label and
  is_cxx)` — only in C++ mode does an asm label's spelling carry the
  implication of a deliberate identity override; in C mode it resolves to
  the same `("extern_c",)` identity as its unlabeled sibling, matching
  castxml.
