### Fixed

- **The CastXML version gate now rejects a prerelease of the next
  unsupported release line** (e.g. `0.8.0-rc1`, `0.8.0.dev1`). A plain
  PEP 440 comparison sorts these below the final `0.8.0`, so they slipped
  past both the minimum and maximum bound checks and would have run as an
  unvalidated build. The upper-bound check now compares the version's
  release segment alone (ignoring pre/dev/post/local qualifiers) against
  the maximum.
- **The C++20 concept-type-shadow check no longer looks inside `//`
  comments.** It was computed before per-line comment stripping, so a
  `// struct concept {};` comment could make a genuine concept
  declaration elsewhere in the same header look ambiguous and get
  incorrectly rejected.
