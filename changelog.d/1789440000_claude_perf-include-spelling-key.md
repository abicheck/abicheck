### Fixed

- A header-backed `compare` given relative `-I` roots parsed each side's
  headers twice: #1402 absolutized the primary pass's include roots, but the
  header-graph attach still keyed its AST acquisition on the raw spelling, so
  it missed the primary pass's entry and fell back to streaming the whole AST
  off disk. The clang and CastXML acquisitions now canonicalize the `-I`
  spelling themselves, so every caller shares one key for one header set
  (public oneDAL `kmeans.hpp` leg: 4 keys to 2, 102 s to 66 s wall, report
  byte-identical).

### Changed

- Faster policy/report passes on large finding sets: the anti-pattern
  annotation indexes anti-patterns once per comparison instead of scanning
  them twice per finding, a selector's `expires` check no longer calls
  `date.today()` per evaluation, and the header graph's shared-attrs
  flyweight builds its key in one pass.
