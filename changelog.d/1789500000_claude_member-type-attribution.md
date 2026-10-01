### Fixed

- **`--contract exports` no longer treats the C tag idiom as ambiguous.**
  `typedef struct Widget {...} Widget;` records a typedef whose target is
  the one record of the same name, and `export_surface` counted that as a
  name collision. Every such type was therefore undecidable under
  `--contract exports` (`identity_ambiguous`) even when an exported
  function plainly took it. A typedef spelled as its own tag
  (`X`, `struct X`, `union X`, `enum X`, `class X`) over a single
  record/enum of that name is no longer ambiguous; every other collision
  still is.

### Added

- **A directory `compare` now says which members a shared type finding
  belongs to.** When several members report the same header-derived type
  change, the product-level entry in
  `public_surface_reconciliation.shared_findings[]` gains an `attribution`
  object partitioning the reporting members into `reaches`,
  `proven_unreachable` and `unestablished`, from each member's own export
  surface. `affected_libraries`, counts, verdicts and exit codes are
  unchanged; Markdown renders the partition in place of the flat
  "affects:" list. Release schema `1.11`.
