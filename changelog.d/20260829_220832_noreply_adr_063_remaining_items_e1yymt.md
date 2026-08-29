### Fixed

- **A function-pointer-returning function's own exception specification
  no longer leaks into its reported return type.** The clang header
  backend's return-type resolver's spiral-declarator branch appended the
  returned function type's own trailing group verbatim, which could
  itself be followed by the outer function's own
  `noexcept(...)`/`throw(...)` exception specification — polluting
  `return_type` with that text and risking a spurious return-type-changed
  finding whenever only the exception-specification condition changes,
  not the actual return type.
