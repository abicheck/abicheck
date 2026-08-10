### Fixed

- **`callback_graph.py`'s function-pointer shape regex rejected a
  cv-qualified pointer**: `void (*const)(int)` (the desugared spelling of a
  `const`-qualified typedef'd function-pointer parameter) was rejected
  outright, silently omitting the registration. The regex now accepts a
  top-level `const`/`volatile` between `*` and `)`.
- **`fold_virtual_dispatch_graph` stamped both the full-coverage and the
  narrowed/degraded coverage keys for the same pass**: the earlier
  narrowed/degraded-propagation fix added those stamps but left the prior
  unconditional full-coverage stamp in place alongside them, contradicting
  the persisted coverage contract every sibling clang-backed pass already
  enforces. Now mutually exclusive, matching the sibling passes'
  `if fully_covered: ... elif narrowed: ... elif degraded: ...` shape.

### Known issues

- **`call_graph.py`'s qualified-call detection (`_member_expr_is_qualified`)
  can misfire on legal whitespace/comments** between a member-call receiver
  and the member name (`obj . f()`), misclassifying it the same way a real
  `Base::` qualifier would. Closing this needs the actual source text
  between two AST offsets, which the pure-AST-dict parser doesn't have
  access to; documented and pinned by a dedicated regression test rather
  than patched with an unsound numeric threshold.
