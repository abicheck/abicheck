### Fixed

- **A spiral (function-pointer-returning) function's own exception
  specification is now correctly separated from its RETURNED function
  type's exception specification, instead of conflating the two.** A
  previous fix in this same change wrongly assumed any exception
  specification following a spiral return type's trailing parameter-list
  group belonged to the outer function and stripped it — but direct
  compilation confirms the opposite: `template<class T> int (*f(T))(int)
  noexcept(noexcept(T()));`'s trailing `noexcept(...)` describes the
  RETURNED function pointer type (`f` itself is not noexcept), so
  stripping it hid a real return-type difference between differently
  conditioned overloads. Conversely, a spiral function's OWN exception
  specification — attached directly after its own parameter list, e.g.
  `template<class T> int (*g(T) noexcept(noexcept(T())))(int);` — was
  still leaking into the reported return type whenever the condition was
  complex enough to introduce its own parentheses, since a span-count-only
  rule mistook the function's own parameter list for a further-nested
  spiral level needing recursion. Both are now resolved correctly by
  discriminating on what immediately follows the wrapper's own nested
  parameter list, not on how many parenthesized groups happen to be
  present.
