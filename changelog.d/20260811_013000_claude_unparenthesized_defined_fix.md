### Fixed

- **`macro_graph.py` didn't recognize `#if defined X` without parentheses**
  (only `#if defined(X)`) — both are valid, real-compiler-accepted
  preprocessor syntax. The unparenthesized form fell through to the
  unmodeled fallback with no diagnostic, silently omitting a real guard's
  `MACRO_CONTROLS_DECL` edge. `_IF_DEFINED_RE`/`_IF_NOT_DEFINED_RE` now
  accept both forms; a malformed, mismatched operand (`defined(X` or
  `defined X)`) still falls through rather than being guessed at.
