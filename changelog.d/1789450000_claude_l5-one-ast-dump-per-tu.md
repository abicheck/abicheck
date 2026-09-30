### Performance

- **`--depth source` dumps each translation unit once for all L5 graph
  passes.** The call, type, override, template, macro-range and callback
  passes each ran their own `clang -ast-dump=json` over every compile unit,
  with identical arguments. They now share one dump per unit and apply their
  own parser to it; results and diagnostics are unchanged. See
  `docs/contribute/plans/l4-l2-extraction-convergence.md` for measurements
  and the remaining L2/L4/L5 convergence phases.
