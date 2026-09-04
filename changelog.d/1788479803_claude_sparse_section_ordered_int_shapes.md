<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`storage`'s sparse-section shape validation no longer accepts a `set`
  for an ordered field, or a fractional value for `source_size`** —
  `ast_compile_args` (a real compiler invocation's ordered argument list)
  previously accepted a `set`, letting canonical form's own sorting invent
  an argument order that was never real; `source_size` (an `int`, matching
  `Path.stat().st_size`) previously accepted any number, including a
  fractional value that would later break a binary-identity comparison
  expecting an exact integer. `build_context_defines` (the one genuinely
  unordered field) still accepts a `set`.
