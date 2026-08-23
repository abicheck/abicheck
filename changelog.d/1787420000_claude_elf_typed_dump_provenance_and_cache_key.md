### Fixed

- **The typed ELF dump path never forwarded its own genuinely explicit
  `public_include_search_dirs` into the actual header parse or header-graph
  attach.** `_run_dump_uncached()` computed `_public_include_search_dirs`
  (falling back to the possibly build/source-evidence-widened `includes`
  only when the caller didn't distinguish the two) but its ELF branch's
  calls to `_dump_elf()` and `_attach_header_graph()` both still used the
  widened value directly — silently reintroducing the exact
  already-widened-includes false-`PUBLIC_HEADER` regression this parameter
  exists to prevent, one call layer above where it was previously fixed.
  Fixed by threading the parameter through `_dump_elf()` (a new keyword
  argument, defaulting to today's unchanged behavior when omitted) and
  using it for both the primary header parse and the header-graph attach's
  node-visibility classification, matching the PE/Mach-O path's existing
  behavior.
- **The whole-snapshot dump cache's key conflated an omitted
  `public_include_search_dirs` (falls back to `includes`) with an
  explicitly empty one (disables provenance widening entirely).** Both
  previously reduced to identical empty-string key material via a bare
  `... or []`, so two otherwise-identical cacheable dumps differing only in
  which of the two the caller meant could share a persisted snapshot even
  though their declarations receive different provenance origins. Fixed by
  encoding presence separately from content in both of
  `_dump_cache_extra_key()`'s branches (the AST and binary-only "no-ast"
  paths alike).
