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
