### Fixed

- `experimental_removed_without_replacement` no longer fires when an
  experimental/preview function is promoted to a stable namespace whose path
  differs by more than the experimental segment (e.g. oneCCL's
  `ccl::preview::split_communicators` -> `ccl::v1::split_communicators`, where
  `v1` is a plain, non-inline namespace). A removal is now treated as a
  promotion when exactly one newly added, non-experimental function under the
  same top-level namespace has the same leaf name and parameter signature;
  ambiguous, cross-root, pre-existing, or signature-less candidates still
  report the removal.
