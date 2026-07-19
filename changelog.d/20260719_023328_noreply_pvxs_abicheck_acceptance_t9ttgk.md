### Fixed

- **Two real-world false positives found in a pvxs acceptance spike are
  fixed.** (1) Type matching across old/new snapshots now prefers
  `RecordType.qualified_name` over the deliberately-bare `RecordType.name`
  when building the old/new comparison maps in `diff_types.py`, so two
  unrelated types that only share a short/leaf spelling (e.g. two distinct
  `std::*::_Impl` template internals pulled in transitively) no longer get
  cross-matched and diffed against each other, producing spurious
  `type_field_removed`/`type_field_added`/`type_base_changed` findings. (2)
  `canonicalize_type_name` now strips the absolute `at <path>:<line>:<col>`
  location clang's `-ast-dump=json` frontend embeds in an anonymous
  struct/union/enum field's type spelling, so comparing an old checkout root
  against a new checkout root of the identical declaration no longer reports
  a false `type_field_type_changed`. The qualified matching key stays
  entirely internal to old/new type matching (via a new `_TypeMap` wrapper
  with a collision-safe bare-name compatibility alias for legacy/schema-
  evolution snapshot pairs): emitted `Change.symbol`, `dumper_hybrid`'s
  per-fact provenance lookups, and `diff_filtering`'s DWARF<->AST redundancy
  correlation all continue to see the bare declaration name they always
  have.
