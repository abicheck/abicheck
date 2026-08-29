### Fixed

- **A `extern "C++"` block nested inside `extern "C"` is no longer
  misidentified as C linkage.** The clang header-AST walk propagated
  C-linkage state with a sticky OR, so a nested `extern "C++"` block never
  reset it back to C++, causing a genuinely-mangled declaration to be
  misclassified as `extern "C"` and collapse onto the bare `("extern_c",)`
  identity, colliding with every other C-linkage declaration. Fixed by
  having a `LinkageSpecDecl` reset the linkage state to its own declared
  language instead of only ever adding `True`.
