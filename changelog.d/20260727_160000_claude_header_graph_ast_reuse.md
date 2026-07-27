<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Performance

- **The always-on L2 header-only semantic graph (G31 Phase A) no longer pays a second, independent `clang -ast-dump=json` parse on `--ast-frontend clang` dumps.** `dumper._clang_header_dump`'s AST is now memoized in-process (`dumper_cache.load_cached_ast`/`store_cached_ast`), so `service._attach_header_graph`'s own AST pass reuses the exact dict the main snapshot pass already parsed instead of re-reading and re-parsing a potentially multi-GB cached AST a second time. This was the source of a large (multi-minute) wall-clock regression on large header trees introduced when the header graph became unconditional. The `castxml` default backend is unaffected (it never called `_clang_header_dump` to begin with).
