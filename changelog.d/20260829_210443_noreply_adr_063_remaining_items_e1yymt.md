### Fixed

- **Two function templates differing only in a function-pointer/
  reference-returning DECLARATOR SHAPE (`typename T::x f(T);` vs.
  `typename T::x (*f(T))(T);`) are no longer treated as identical.**
  `_return_type` used to treat the first top-level parenthesized group in
  a function's `qualType` as its parameter list unconditionally, but a
  function-pointer/reference return type is itself spelled as a spiral
  declarator wrapping the real parameter list one level deeper — so both
  overloads' return type collapsed onto the identical spelling, discarding
  the one thing distinguishing two legal, coexisting overloads. The real
  parameter list is now located by recursing into the wrapping declarator
  until no further top-level group follows, so the declarator's shape
  (pointer vs. reference, and any further nesting) is preserved.
