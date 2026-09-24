### Performance

- A header-only `dump` (no binary) no longer builds model objects for the
  dependency-header functions and variables that the default dependency
  exclusion discards afterwards. The output is unchanged. On Intel SVS
  (76 header roots) this skips 106,815 declarations and cuts dump wall time
  by about 21%. A caller opts in by declaring, around extraction, the root
  set it will scope with (`extract.dependency_exclusion`); `dump` and
  `compare`'s live dumps do. Dumps with a binary are unaffected: their
  pre-scoping surface graph records dependency declarations too.
- `dump_manifest_header_roots` and `dump_manifest_public_roots` moved from
  `dumper_scoping` to `extract.dump_manifest_roots`.
