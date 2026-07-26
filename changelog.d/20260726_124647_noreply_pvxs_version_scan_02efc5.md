### Fixed

- **F8 additive-only header-set carve-out now actually works end-to-end** —
  the previous fix correctly stopped the scope carve-out from skipping the
  profile check, but that immediately exposed that a pure header addition
  also changes `profile_fields["header_sequence"]` (declared-header order,
  tracked separately from scope's declared set), so
  `check_contracts_comparable` still raised `ProfileMismatchError` on the
  identical real-world scenario. Added a second, symmetric carve-out
  (`_header_sequence_is_additive_reorder_free`): a `profile_fingerprint`
  mismatch confined to `header_sequence` alone does not raise when the new
  sequence, with exactly the newly-added headers removed, reconstructs the
  old sequence exactly — proving no existing header was reordered, only
  new ones appended/inserted. A reorder entangled with growth still
  raises. Verified end-to-end against the real pvxs F8 scenario with both
  carve-outs together.
- **`std::tuple_size` recognized as a user-specializable customization
  point** — another gap in `is_stdlib_local_name_symbol()`'s allowlist
  (`name_classification.py`), the same class as the earlier `std::hash`/
  `std::swap` gaps: a program-defined `std::tuple_size<MyType>`
  specialization's local static was wrongly classified as stdlib-owned.
