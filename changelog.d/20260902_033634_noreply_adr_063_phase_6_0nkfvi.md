### Fixed

- **`semantic_ir`'s closure/anonymous-marker renumbering now covers its
  `SemanticIR.occurrences` dict keys and `semantic_ir_conflicts`, not just
  plain field values** — `renumber_anonymous_closure_identities()`'s
  generic string walk previously left a dataclass-typed dict key
  (`OccurrenceId`) untouched, so an anonymous/lambda-bearing occurrence's
  identity stayed on its raw, line-tainted spelling even after the
  identical marker text was correctly renumbered everywhere it appeared as
  a plain field value. `semantic_ir_conflicts`'s own packed keys are
  re-derived rather than text-patched in place, since an in-place rewrite
  would corrupt their length-prefixed encoding.
