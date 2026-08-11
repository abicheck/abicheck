<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A genuine castxml constant/default value could be silently mistaken for
  a stale clang fingerprint and its real change dropped** (`diff_default_
  value_reliability.py`): both `default_value_fingerprint_comparison_
  unreliable` (`Param.default`/`TypeField.default`) and the newly-added
  `constant_value_fingerprint_comparison_unreliable` identified a clang
  structural-expression fingerprint by checking only the `"expr:"` PREFIX.
  castxml keeps a constant's or default's value as verbatim source text
  (`dumper_castxml._iter_public_constants` passes its raw XML `init` text
  straight through), so a real declaration referencing a qualified name
  whose next component happens to spell `expr` (an expression-template
  library's `expr::` namespace, say) produces a legitimate value like
  `"expr::OLD_VALUE"` — which the prefix check misidentified as a clang
  fingerprint and could silently suppress a genuine
  `CONSTANT_CHANGED`/`PARAM_DEFAULT_VALUE_CHANGED`/
  `FIELD_DEFAULT_INITIALIZER_CHANGED`. Both functions now match the full
  fingerprint shape (`"expr:"` plus exactly 16 lowercase hex digits, the
  exact output of `dumper_clang_expr._expr_fingerprint`) via a new shared
  `_is_expr_fingerprint` helper.
