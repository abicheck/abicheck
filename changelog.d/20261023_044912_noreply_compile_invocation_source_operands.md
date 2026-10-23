<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->
### Added

- **Per-source operands for compile-database entries** — the compile-invocation
  model now parses each GNU-driver command into one `SourceOperand` per source
  (its own language, action and effective output), plus the link inputs a link
  consumes. Entries such as `gcc -c a.c b.c` or `g++ -x c++ a.h -x none b.c`
  are no longer reduced to their first source. Renderers are unchanged.
