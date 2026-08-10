### Fixed

- **`call_graph.py` didn't classify a virtual overloaded operator invoked
  through a base reference/pointer as virtual** (`B &b; b();`): this is a
  `CXXOperatorCallExpr`, not a `CXXMemberCallExpr`, so the existing
  virtuality check never applied, silently excluding a real derived
  `operator()` override from `VIRTUAL_CALL_MAY_DISPATCH_TO`. Fixed with two
  pieces: `_classify_call` now also checks `CXXOperatorCallExpr`, and
  `_find_referenced_decl` now upgrades a `DeclRefExpr`'s compact stub to the
  full declaration node (when already indexed) the same way a `MemberExpr`'s
  string-shaped reference already was — the stub alone never carried the
  `virtual`/override-attribute fields the classification needs.

### Documentation

- Investigated and documented (not fixed) a genuinely deep gap in
  `macro_graph.py`: backslash-newline line splicing before a `//` comment
  is unmodeled, so a directive-looking line spliced into a still-open
  comment reads as live. Pinned by a dedicated regression test.
