### Fixed

- Type reachability no longer misses a registered spelling that starts at the
  same offset as a longer one -- a class template inside its own
  instantiation (`dal::Table` in `dal::Table<float>`), `std::vector` in
  `std::vector<int>`, `Node` in `Node*`. `compare/spelling_pattern.py`'s
  `finditer_allow_nested` now enumerates every boundary-valid candidate at
  each offset, re-checking the right boundary on the real text so a shorter
  spelling is never accepted inside a longer identifier (`Foo` in `Foobar`).
  This adds reachability edges, so it can add findings where a type was
  previously reachable only through such a spelling; the golden snapshots,
  the FP-rate gate and the per-tier accuracy gate are unchanged. Closes the
  `finditer_allow_nested` entry in `docs/contribute/known-gaps.md`.
