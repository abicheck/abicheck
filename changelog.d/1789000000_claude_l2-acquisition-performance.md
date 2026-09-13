### Performance

- **AST cache keys no longer walk include trees through `Path.rglob`.**
  `header_utils.iter_cache_header_files` -- the include-root scan behind
  `dumper_ast_config._cache_key` and `snapshot_cache`'s whole-snapshot key --
  now traverses with `os.scandir` and sorts relative path *parts*, instead of
  materializing and sorting one `Path` object per entry visited. Measured
  ~2.4-3x faster key computation on a large installed include tree (22k
  matching entries) with a byte-identical cache key: the walk keeps
  `rglob("*")`'s exact semantics, including component-wise ordering,
  suffix-named directories as entries, and not descending into symlinked
  directories.
- **The clang AST cache write no longer pays the pure-Python JSON encoder.**
  `json.dump` never reaches the C encoder -- `JSONEncoder.iterencode` only
  selects it under `dumps`' one-shot path -- so the DPC++ host/device cache
  write was encoding a multi-hundred-MB AST a fragment at a time in Python.
  `dumper_cache._atomic_write_json` now descends the large containers itself
  and hands each bounded subtree to the C encoder whole: byte-identical
  output, peak transient memory still bounded (so the multi-GB tree is never
  duplicated the way `json.dumps(obj)` would), and measured 5x faster on a
  deep-template AST fixture. The plain (non-DPC++) path still streams a raw
  file copy and is untouched.
- **Per-header `clang -M` include extraction now runs in parallel.**
  `ClangIncludeExtractor.extract_from_build` -- which the L2 per-header
  include closure (`header_graph.ClangHeaderIncludeExtractor`) drives one
  synthetic compile unit at a time -- plans every invocation up front, runs
  them through a bounded worker pool, then folds the outcomes back in
  compile-unit order, so the include map, the diagnostics and their order are
  unchanged for any worker count. Measured 1.7s -> 0.44s for 32 headers on a
  4-CPU host, with byte-identical depfiles. Worker count comes from the
  shared CPU/RAM sizing (`process_resources`) and is overridable with
  `ABICHECK_INCLUDE_MAP_JOBS` (`1` restores the sequential pass);
  concurrently spawned `clang -M` children are additionally capped
  process-wide by a single gate sized from the host budget alone, so a
  `compare` resolving both sides at once cannot oversubscribe the host even
  when the two sides' header counts (and so their pool sizes) differ.
