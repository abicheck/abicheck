### Fixed

- **`--ast-frontend clang` mis-numbered enumerators initialized from a
  sibling enumerator** (e.g. `enum { A, B, X = B };`), reporting every such
  alias — and every implicit enumerator after it — under its wrong,
  positional auto-increment value instead of its real one, and diffing those
  wrong values as spurious `enum_member_value_changed` findings. Root cause:
  `_evaluated_int_value` only checked the original AST node and the fully
  unwrapped leaf expression for a folded `value`; clang folds an
  enumerator-alias initializer's real value onto an intermediate
  `ConstantExpr` wrapper it then continues descending through (into a
  `DeclRefExpr` naming the aliased enumerator, which carries no `value` of
  its own), so that intermediate value was silently lost. `dumper_clang.py`'s
  `_evaluated_int_value` now checks every node along the same unwrap chain,
  not only its endpoints. Reproduced against a real oneDNN 3.11→3.12
  `dnnl_format_tag_t` comparison, which previously reported 787 false
  `breaking enum_member_value_changed` findings under the clang frontend.
