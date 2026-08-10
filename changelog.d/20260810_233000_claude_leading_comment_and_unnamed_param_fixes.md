### Fixed

- **`macro_graph.py`'s directive scanner missed any directive preceded by a
  leading, same-line-closed block comment** (`/* note */ #ifdef X`): a real
  compiler treats the comment as whitespace and accepts the guard, but every
  directive-family regex is anchored `^\s*#`, so a leading comment defeated
  them all — nested inside another guard, this also desynced the enclosing
  guard's own nesting depth. `_strip_leading_inline_comment()` now strips a
  same-line-closed leading `/* ... */` before matching, applied identically
  to `scan_conditional_regions` and `_macro_definition_lines`.
- **`callback_graph.py` collapsed every unnamed callback parameter across
  the whole codebase onto one graph node**: an unnamed parameter (a common
  prototype-only registration-API shape, `void reg(void (*)(int));`) has no
  name for its slot identity, which previously resolved to the empty
  string — making the otherwise high-confidence `DECL_REGISTERS_CALLBACK`
  edge ambiguous across unrelated registration APIs. The slot identity now
  falls back to the callee's own identity plus the parameter's position
  when the parameter itself is unnamed.
