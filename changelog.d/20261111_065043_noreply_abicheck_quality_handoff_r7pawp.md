### Changed

- **The per-generation spelling table has its own module** — internal:
  `SPELLING_MIGRATIONS`, `GenerationSpellingChange`, `PLACEHOLDER_REASONS` and
  `is_placeholder_reason` move from `abicheck.storage.generation_migrations` to
  `abicheck.storage.generation_spelling_table` (the generation-corpus gate reads
  it there). The table gains a row per extractor generation and had pushed its
  old module past the 800-line cap; loading and migration behaviour is unchanged.
