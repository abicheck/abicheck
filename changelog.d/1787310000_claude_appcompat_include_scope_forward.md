### Fixed

- **The standalone `check_appcompat()` Python API dropped a caller's
  explicit `old_includes`/`new_includes` (or the shared `includes=`
  fallback) instead of forwarding them to `dump()`'s declaration-
  provenance widening.** Both of its `dump()` calls passed the caller's
  own genuinely explicit `-I` list only as `extra_includes` (the compile
  include path), never as the separate `public_include_search_dirs`
  parameter this PR introduced for the CLI's compare/dump/scan frontends
  — so a declaration reached only through an explicit include root stayed
  `PRIVATE_HEADER` on this one API and could be scoped out of the diff
  under the default `scope_to_public_surface=True`, reproducing the same
  false-clean result already fixed elsewhere. Fixed by forwarding each
  side's own explicit include list as `public_include_search_dirs` too.
