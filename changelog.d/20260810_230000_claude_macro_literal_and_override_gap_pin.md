### Fixed

- **`macro_graph.py`'s block-comment scanner mis-tracked a `/*`/`//` inside
  an ordinary string or char literal** (e.g. `const char *token = "/*";`),
  silently entering block-comment state and hiding every following line —
  including a real `#ifdef`/`#endif` pair — with no diagnostic to signal
  the desync. `_lines_starting_inside_block_comment()` now also tracks
  string/char-literal state (with escape-character handling); a raw string
  literal (`R"(...)"`) stays a documented, unhandled residual case.

### Documentation

- Pinned two already-documented `call_graph.py`/`virtual_dispatch_graph.py`
  limitations with dedicated regression tests rather than leaving them
  undertested: `_ref_is_virtual`'s known false negative for an implicit
  override with neither `override`/`final` nor a repeated `"virtual": true`
  (new `tests/test_call_graph_extra.py`, a sibling split of
  `test_call_graph.py`), and the vtable-presence seeds' shared
  join-only-onto-an-existing-node limitation for a fully isolated
  polymorphic class.
