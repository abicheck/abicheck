<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Security

- **Namespace-suppression globstar matching could stall on a pathological
  candidate name.** A `namespace`/`entity_namespace`/`cause_namespace`
  selector chaining several non-adjacent `**` segments (e.g.
  `"**::a::**::a::**::a::**::a::**::a::z"`) compiled to a single regex with
  one independently-backtracking group per globstar — combinatorial for the
  `re` engine against a long, repetitive-content candidate name (a real, if
  unusual, worst case for a deeply-templated/generated C++ symbol); a
  121-segment non-matching name took over 8 seconds to reject a single
  match, and suppression matching runs across every finding in a
  comparison. `_compile_namespace_glob` now detects a namespace pattern
  built only from literal segments and `**` globstars (no per-segment
  `*`/`?`/`[...]`) and matches it with a linear segment-by-segment
  dynamic-programming matcher instead of a backtracking regex — provably
  equivalent for that pattern shape and immune to the blowup. A pattern
  with an embedded per-segment wildcard keeps the existing regex path
  unchanged.
