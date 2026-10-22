### Performance

- **The CPO detector derives each declaration's identity once.** Its
  cross-kind reconciliation keyed every function and variable of both sides
  through the qualified-name identity about four times (public lists, full
  lists, alias index, both passes). The identity is now computed once per
  declaration for the pair: `qualified_declaration_name`/`demangle_one_batched`
  calls per 10 declarations drop 63 -> 43 (`tests/perf_call_budgets.json`
  re-recorded), and the reconciliation went 1.27 s -> 0.86 s on the synthetic
  sweep. Findings unchanged.
