### Performance

- **Faster snapshot encode and load** — the write-path encoder dispatches on
  a per-class precompiled encoder instead of re-running an `isinstance`
  chain per value, `canonical_form_shared`/`is_canonical_tree` skip calls
  for self-canonical leaves, and the decoder memoizes `Param.kind_fact`
  with its `ParamKind` rebuild. Output, `snapshot_content_digest` and
  on-disk bytes are unchanged; on a real MKL cache entry load drops from
  ~11.4 s to ~10.0 s and building the write document from ~8.1 s to ~7.3 s.
