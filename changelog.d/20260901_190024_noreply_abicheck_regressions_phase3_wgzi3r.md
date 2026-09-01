### Fixed

- **`compare` could crash with `AttributeError: 'str' object has no
  attribute 'value'` in the `removed_const_overload` detector on any
  snapshot containing a closure/anonymous-type marker anywhere.**
  `Param.kind` is a `ParamKind(str, Enum)`, which satisfies
  `isinstance(value, str)`; the closure-marker rewrite walk
  (`qualified_name_segments._collect_strings`/`_walk_rewrite_strings`,
  driven on every snapshot load by `storage.snapshot_load_normalization`)
  treated it as ordinary free text and handed it to
  `name_classification.strip_anonymous_type_location`, whose `re.sub` call
  returns a plain `str` even on zero substitutions — silently downcasting
  every `Param.kind` reachable from a marker-bearing snapshot. Both walk
  primitives now treat any `str`-subclass `Enum` member as opaque, closing
  the same downcast for any such field, not just `ParamKind`.
- **A checkout-dependent absolute path could leak into an L5 source-graph
  node identity (and a stripped flat closure-marker spelling), producing a
  spurious `declaration_renamed` between two builds of an unedited
  declaration.** `_BARE_ANON_TYPE_LOCATION_RE` and
  `_ANON_TYPE_LOCATION_PATH_ONLY_RE` recognized the `lambda`/`unnamed
  <kind>` anonymous-tag vocabulary but not the `anonymous <kind>` spelling
  a real corpus was observed to carry; both regexes (plus
  `qualified_name_segments`'s ordinal-renumbering marker prefix and its
  marker-detection guard) now also recognize `anonymous <kind>`.
