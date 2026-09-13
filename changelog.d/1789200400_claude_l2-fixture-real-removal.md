### Fixed

- **The full-CLI L2 fixture's "removed function" dropped only the definition,
  not the header declaration.** The `new` side's header still promised
  `shape_count()` while the export table no longer carried it — a header/binary
  disagreement rather than a public API removal. It still satisfied the
  removal-family assertion (as `func_removed_elf_only`), so the suite passed
  while the fixture was not the scenario its own docstring described. The
  declaration is now removed too, and the comparison reports a genuine
  `func_removed` plus `public_surface_shrank`. Caught by the real-subprocess
  fixture test rather than by any of the logic-level ones, which is the case for
  writing it against real compiled output.
