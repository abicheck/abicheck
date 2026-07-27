### Fixed

- **`std::common_type` closed as the fourth instance of the
  user-specializable-customization-point allowlist gap** — a program-defined
  `std::common_type<A, A>` partial specialization contains user-authored
  code (same shape as the earlier `std::hash`/`std::swap`/`std::tuple_size`
  fixes), so `is_stdlib_local_name_symbol()` must not classify its local
  statics as stdlib-owned. Added to
  `_USER_SPECIALIZABLE_STD_TEMPLATE_RE`'s allowlist; documented in the
  regex's comment as an accepted, permanent limitation of a finite
  allowlist rather than an ongoing punch list.
