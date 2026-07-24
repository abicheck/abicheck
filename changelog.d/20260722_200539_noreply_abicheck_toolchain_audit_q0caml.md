<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The C++20 trailing-`requires` heuristic no longer picks up an
  unrelated `->` from an earlier statement.** `_looks_like_genuine_requires_
  clause` and `_looks_like_requires_declarator`
  (`abicheck/dumper_ast_config.py`) searched for a declarator-continuation
  signal (`->`/`)`/`>`) anywhere in the same-line prefix or the previous
  logical line, both of which can hold more than one statement. A plain
  pre-C++20 declaration like `auto x = p->m; requires value;` (declaring
  `value` with type `requires`) was wrongly classified as a genuine C++20
  trailing requires-clause because of the unrelated `->` in the earlier
  member access, forcing `-std=gnu++20` and rejecting the otherwise-valid
  header. Both checks now stop at the nearest statement boundary
  (`;`/`{`/`}`) before searching.
</content>
