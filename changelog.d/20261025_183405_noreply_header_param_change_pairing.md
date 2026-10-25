### Fixed

- **A header-declared parameter retype is a parameter change, not a lone
  addition** — when both sides are parsed from headers and a non-template
  function keeps its header declaration (same name, same written parameter
  and return spellings, same header) while a parameter's resolved type
  changes, `compare` now reports one `func_params_changed` on the old symbol
  instead of `func_added` for the new mangling with no matching removal. A
  typedef whose target changed, a resolved record that changed, or a default
  template argument that picks a different width (`V scale(V, float)` with
  `using V = xvec::simd::vec<float>` built for AVX2 and then AVX-512) all
  count. The verdict stays BREAKING, because the old symbol is gone. A
  target-ISA change still annotates the finding. A changed written signature
  (a parameter retyped to an internal type) stays `func_removed`, and so does
  a mangling change the header cannot explain (a renumbered lambda closure).
