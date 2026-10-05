### Performance

- `compare`: the experimental-namespace promotion check builds its OLD/NEW
  declaration sets once per detector call instead of once per removed
  declaration, and qualified-name segmentation is memoized
  (`diff_namespaces`, `compare.qualified_name_normalization`) -- previously
  ~3.2 M re-segmentations over ~17 k distinct names on a large C++ library.
- Snapshot load skips the anonymous-type location-strip walk when no
  collected string carries a raw `(lambda at <path>:L:C)`-style spelling,
  reusing the closure marking instead of re-walking every string field.
- `policy_kind_sets` is memoized per policy name, and `resolve_kind_sets`
  no longer copies the four kind sets on every call.
- DWARF calling-convention extraction reads each formal parameter's
  `DW_AT_type` straight from the raw `.debug_info` bytes instead of building
  a pyelftools DIE per parameter, and memoizes the by-value trait per
  referenced type (the advanced DWARF pass: 3.8 s -> 2.3 s on a 60-module C++
  fixture; output identical on GCC DWARF 4/5 and clang DWARF 5).
- DWARF low-memory mode (free each CU's DIE cache after use) is now on for
  every binary: `ABICHECK_DWARF_LOW_MEMORY_MB` defaults to `0` instead of
  `32`. Measured faster and lower-peak at every size tried; set it to `-1`
  to keep the old retain-everything behaviour.
- Closure-identity renumbering descends `SemanticIR.occurrences` entry by
  entry instead of rewriting the whole mapping whenever any occurrence holds
  a closure marker: the rewrite step on a 60-module clang dump fell from
  2.0 M walked nodes / 1.6 s to ~21 k nodes / 0.15 s, byte-identical output.
