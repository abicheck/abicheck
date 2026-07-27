### Fixed

- **A comparability-gate unknown-field check missed empty-vs-absent
  schema drift, and the customization-point allowlist missed libc++'s
  inline namespace.** `unknown_differing`/`scope_unknown_differing` fell
  back to `""` for a missing key, conflating "key absent entirely" with
  "key present with an empty string value" — a newer-schema field added
  on only one side with an empty value compared equal and stayed
  invisible even when combined with an otherwise-legitimate, corroborated
  delta. Fixed with a `_FIELD_ABSENT` sentinel distinct from every valid
  field value, used as the `.get()` fallback in both checks. Separately,
  `_USER_SPECIALIZABLE_STD_TEMPLATE_RE` (the user-specialized
  customization-point allowlist in `name_classification.py`) didn't
  account for libc++'s versioned inline ABI namespace (`__1`/`__ndk1`)
  between the `std::` substitution and the class name, so a user
  specialization of e.g. `std::hash<X>` under libc++ was wrongly
  classified as stdlib-owned and its alignment regression suppressed.
  Fixed with a non-greedy optional inline-namespace component in the
  regex; the Hypothesis grammar suite is extended to cover this shape
  generatively.
