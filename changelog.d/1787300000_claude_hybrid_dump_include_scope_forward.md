### Fixed

- **`dump()`'s hybrid AST frontend (`--ast-frontend hybrid`) dropped a
  caller's explicit `-I`/`--include` public-scoping roots instead of
  forwarding them to its two recursive sub-dumps.** The declaration-
  provenance widening this same PR fixed for the castxml and clang
  backends individually (`public_include_search_dirs`) never reached
  `dumper_hybrid.run_hybrid_dump()`'s own castxml/clang delegation calls,
  since `dump()`'s hybrid branch built its kwargs without it — so a header
  reached only through an explicit `-I` stayed `PRIVATE_HEADER` and could
  be scoped out under `--ast-frontend hybrid`, reproducing the exact
  false-clean result already fixed for the single-backend case. Fixed by
  passing `public_include_search_dirs` through to `run_hybrid_dump`, whose
  existing `**kwargs` forwarding already relays it unchanged to both
  recursive `dump()` calls.
