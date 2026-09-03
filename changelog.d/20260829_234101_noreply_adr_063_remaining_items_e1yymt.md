### Fixed

- **A `decltype` return type whose operand is an address-of expression
  (e.g. `decltype(&(S::x))`) is no longer mistaken for a function-
  pointer/reference-returning ("spiral") declarator.** A previous fix
  closed this hazard for a dereferenced-cast operand by checking that
  what follows a candidate spiral wrapper's own nested group looks like
  real declarator structure, but an address-of expression with an EMPTY
  remainder is textually indistinguishable from a genuine
  reference-returning spiral declarator with no parameters by that check
  alone. Fixed generally: any group whose immediately preceding text is
  the bare `decltype` token is now always treated as that operator's own
  operand, never a declarator wrapper, regardless of what the operand's
  own text happens to start with.
