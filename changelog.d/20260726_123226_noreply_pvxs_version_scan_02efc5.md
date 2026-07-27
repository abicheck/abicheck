### Fixed

- **F8 additive-header-set carve-out no longer skips the profile check** —
  `check_contracts_comparable` waived an additive scope mismatch (F8) with
  an early `return None`, which also silently skipped the subsequent
  `profile_fingerprint` check; a release that both adds a header and
  changes an unrelated, uncorroborated extraction-profile field (compiler
  family, macros, include order) would have been wrongly treated as fully
  comparable instead of raising `ProfileMismatchError`. The carve-out is
  now gated into the scope condition itself, so it only ever widens the
  scope check, never bypasses the profile check that follows.
- **`std::swap` recognized as a user-specializable customization point** —
  `is_stdlib_local_name_symbol()`'s allowlist (`name_classification.py`)
  was missing `std::swap`, a *function* template the standard explicitly
  permits specializing for a program-defined type
  (`template<> inline void std::swap<MyType>(...)`); a local static inside
  such a specialization was wrongly classified as stdlib-owned and its
  alignment regression silently suppressed.
