### Fixed

- The clang header-graph attach now reads and warms its projection sidecar
  when the AST came from the request's acquisition table. A retained-table
  hit bypassed `load_cached_ast`, the only place a cache entry was offered to
  a derived-AST consumer, so the attach re-projected the tree every run and
  never stored a sidecar (`dumper_cache.run_ast_acquisition_offering_entry`).

### Performance

- The override-only `VIRTUAL_METHOD` pattern-facts rule matches its leading
  whitespace atomically; the backtracking form cost 13.4 s over oneDAL's 604
  headers, 0.25 s now, identical matches.
- `renumber_anonymous_closure_identities` collects strings and flags
  marker-bearing subtrees in one traversal, then rewrites only flagged
  subtrees (15.6 s to 6.9 s on oneDAL's pre-scoping live snapshot, identical
  output). `_walk_rewrite_strings` caches its per-type field plan.
- Dependency scoping compiles its spelling regex only over spellings whose
  identifier tokens occur in the text being scanned
  (`spellings_possible_in`); the two ~40k-spelling vocabularies cost ~14 s
  per oneDAL compare.
- Loading a stored sectioned snapshot no longer freezes and re-thaws every
  current-version section through a throwaway `SectionDTO`
  (`current_section_payload`), and the graph section skips its own
  freeze/thaw copy for an owned, canonical payload
  (`GraphSection.document_from_owned`). oneDAL baseline load 31.4 s to
  18.1 s, identical snapshot.
- Writing a sectioned snapshot encodes each section once
  (`section_dto_dict`, `GraphSection.validated_document`) and no longer
  re-canonicalizes the finished sections: 28.4 s to 6.6 s for oneDAL's live
  snapshot, byte-identical output.
- zstd snapshots of 8 MiB or more are compressed in zstd's multi-threaded
  mode (up to 8 workers). Its output does not depend on the worker count, so
  stored bytes are the same on any machine; smaller snapshots keep their
  existing bytes. Level 19 on oneDAL: 33.9 s to 21.6 s on four busy cores.
