### Fixed

- **The clang header backend's return-type recovery no longer mistakes an
  unrelated parenthesized group for a function's parameter list.** The
  previous fix for function-pointer/reference-returning declarators
  assumed the first top-level parenthesized group in a function's
  `qualType` was always either the real parameter list or a wrapper
  around it — which broke two other real cases: a dependent return type
  with its own parenthesized sub-expression (`decltype((T::x))`) had that
  sub-expression discarded entirely, and an ordinary function's
  `noexcept(expr)` exception specification got appended onto
  `return_type` as if it were part of the return type. Fixed by scanning
  from the END of the `qualType` for the real parameter-list group
  instead (skipping a group immediately preceded by `noexcept`/`throw`),
  which resolves all of these cases — plus the earlier function-pointer/
  reference-return case — together, correctly and without recursion.
