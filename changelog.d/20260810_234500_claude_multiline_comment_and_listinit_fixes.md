### Fixed

- **`macro_graph.py` still missed a directive when the leading comment
  closing before it opened on an earlier line** (`/* opening\n*/ #ifdef X`):
  the per-line "starts inside a carried-over block comment" gate previously
  skipped such a line wholesale rather than resuming after that specific
  comment's own close — the same missed-guard/nested-desync consequence the
  earlier same-line leading-comment fix addressed for a same-line-only
  comment. `_line_after_carryover_comment_closes()` now finds where the
  carried-over comment ends and resumes matching against the live
  remainder, applied identically to `scan_conditional_regions` and
  `_macro_definition_lines`.
- **`callback_graph.py` didn't recognize C++ list-initialization of a
  callback slot** (`handler_t slot{my_handler};`): the function-to-pointer
  decay is wrapped in an `InitListExpr`, which `_address_taken_function`
  didn't unwrap, silently omitting the `DECL_TAKES_ADDRESS_OF` edge. Now
  unwraps a single-element `InitListExpr`, scoped to one element since a
  scalar type type-checks to exactly one initializer.
