### Fixed

- **A stored snapshot now reports the same undeclared-export census as its live counterpart** —
  the `-E -dD` branch scan that attributes an exported function to the guard macro its
  declaration sits under was runtime-only, so a `compare` of `abicheck dump` outputs fell back
  to the `sycl::` name rule and read a generic `#ifdef` export as unattributed. The scan is now
  persisted as `live_preprocessor_hidden` (snapshot schema v67). Snapshots dumped before this
  version carry no scan and keep the named-cause rule. Several field losses found by the
  per-field round-trip test are also fixed: `decl_file` on structs and enums, Mach-O export
  `is_data`, DWARF `wchar_flags`, `spellings_generation` on a re-save without a fact-generation
  stamp, and non-`PRESENT` `ownership_fact` statuses.
