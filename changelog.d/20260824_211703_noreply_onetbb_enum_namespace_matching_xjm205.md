### Fixed

- **Namespace moves no longer report as type mutations** — old/new type
  matching (`diff_helpers.lookup_matched_type`) retried a failed
  qualified-name lookup by bare declaration name even when *both* sides
  recorded a qualified identity, so a real namespace move (oneTBB 2022's
  `tbb::detail::d1::graph` → `tbb::detail::d2::graph`) paired two distinct
  types and produced phantom `type_size_changed` /
  `type_field_offset_changed` / `type_vtable_changed` findings instead of a
  removal plus an addition. The bare-name retry now applies only to the two
  cases it exists for — a side that genuinely never recorded
  `qualified_name`, and two spellings that differ only by an inline ABI-tag
  namespace (`std::` vs. libstdc++'s dual-ABI `std::__cxx11::`, or a
  versioned `ns::v1::`), which name one entity. An ordinary implementation
  namespace (`detail`, `impl`, `d1`) is never treated as such a tag.

- **A whole-namespace move is recognized as one `symbol_renamed_batch`** —
  the batch-rename detector only understood a *prepended prefix*, so a set
  of symbols whose mangled names differ by one namespace scope component was
  reported as N unpaired `func_removed` next to N unpaired `func_added` with
  nothing linking the two halves. Generic over any shared segment
  substitution; the per-symbol removals are still reported, since a consumer
  linked against the old name really does fail to resolve.

- **Destructors are no longer treated as renamed class names** — the same
  detector paired `Wrapper` → `~Wrapper` and `graph` → `~graph`, reading the
  `~` as an added prefix. A rename pair's two halves must now agree on being
  a destructor, and the prepended text must end at a name boundary.

- **Clang-spelled lambda closure types are classified as non-ABI-surface** —
  `(lambda at <path>:<line>:<col>)` (and its normalized
  `(lambda:<file>:<line>:<col>)` form) matched none of the anonymous-type
  markers, so a template instantiated over a closure carried a source line
  number in its ABI identity: an unrelated edit earlier in the header made
  it read as a whole type removed plus a whole type added at BREAKING
  severity. GCC/DWARF's `{lambda(...)#1}` spelling was unaffected.
