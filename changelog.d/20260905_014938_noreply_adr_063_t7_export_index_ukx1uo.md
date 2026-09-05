### Changed

- **One canonical raw export index, with named projections** — ADR-063 T7:
  `policy/depth_projection.py`, `buildsource/crosscheck_base.py`,
  `buildsource/snapshot_exports.py`, `post_manifest.py`, and
  `diff_unnamed_types.py` each kept an independent copy of "read a
  snapshot's/binary's platform export table," subtly diverging on ELF
  default-version filtering, Mach-O leading-underscore normalization, and
  missing-vs-confirmed-empty table semantics. They now all derive from one
  canonical `model.export_index.build_raw_export_index` raw read plus small,
  independently-tested named projections (`default_versioned_names`,
  `linked_export_names`, `named_pe_exports`, `ordinal_only_pe_exports`,
  `macho_callable_names`, `callable_export_names`, `all_export_names`), with
  no behavior change at any existing call site.
