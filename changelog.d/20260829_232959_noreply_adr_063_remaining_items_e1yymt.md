### Fixed

- **A relational or shift operator inside a paren-wrapped non-type
  template argument (e.g. `std::enable_if_t<(sizeof(T) < 4), int>`) no
  longer corrupts `return_type`'s bracket-depth tracking.** The clang
  header backend's return-type resolver's paren/arrow scanners counted
  every `<`/`>` character as a template-angle-bracket regardless of
  whether it occurred inside an already-open parenthesized group,
  leaving the bracket counter permanently stuck above zero once a
  relational operator appeared after an already-open template argument
  list — silently discarding a real parameter list, a trailing return
  arrow, or an exception specification depending on where the operator
  occurred. Bracket depth is now only tracked while paren depth is also
  zero, matching the grammar rule that such an operator inside a
  template argument list must itself be paren-wrapped to disambiguate
  it from the closing `>`.
