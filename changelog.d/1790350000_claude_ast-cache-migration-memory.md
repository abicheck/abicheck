### Performance

- Pretty-printed clang AST cache entries written before compaction-at-store
  are now migrated to the compact ASCII form on first read
  (`storage.json_compact.migrate_legacy_entry`, called from
  `dumper_cache.load_cached_ast`), atomically and value-preservingly; the
  entry shrinks to ~30% of its size and every later warm read decodes it at
  1 byte per character.
- Lower snapshot memory: `Fact` shares one instance per common valueless
  shape (`present(False)`, `not_collected()`, ...); graph node/edge
  `conflicts`/`occurrences` default to a shared empty tuple; `EntityId`,
  `Namespace`, `Record`, `OccurrenceId`, `CanonicalEntity`, `TypeField` and
  `EnumMember` are now slotted; `strip_anonymous_type_location` is memoized.
