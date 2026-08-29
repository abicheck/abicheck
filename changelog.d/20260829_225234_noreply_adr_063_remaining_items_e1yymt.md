### Fixed

- **A `decltype` return type whose operand happens to start with a bare
  `*`/`&` sigil followed by a parenthesized group (e.g. a dereferenced
  C-style cast, `decltype(*(typename T::x *)0)`) is no longer mistaken
  for a function-pointer-returning ("spiral") declarator.** The clang
  header backend's return-type resolver's spiral-declarator detection
  matched on the leading sigil alone, discarding the entire dependent
  operand as if it were a declarator wrapper and collapsing two
  overloads differing only in that operand onto the identical reported
  return type. The detector now also requires that whatever follows the
  sigil's own nested group look like real declarator structure (nothing,
  an exception specification, or a further-nested parameter list) rather
  than arbitrary expression text.
