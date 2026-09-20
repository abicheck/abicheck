### Changed

- **A cold header-graph attach now streams the clang AST from disk instead
  of parsing it into memory, cutting its peak RSS by about two thirds.**
  Caching the attach's AST projection already made a *warm* run cheap, but
  the first run in CI, the first after a header edit, and every cache-cold
  container still built the whole tree: measured on a 263 MiB
  `clang -ast-dump=json` document, the document is held as one string while
  roughly 400 MiB of dicts is built from it, and that pair — not the tree
  alone — is the peak. `abicheck.buildsource.header_graph_ast_stream` walks
  the translation unit's top-level declarations one at a time and drives the
  same four readers over them, so the whole document is never resident.
  On oneDAL 2024.7's `libonedal_core.so.2` against the full `daal.h`
  surface, a cold attach's peak RSS falls from 2097 MiB to 877 MiB (−58%)
  at about twice its wall time — a deliberate trade, since the attach is
  memory-bound rather than CPU-bound.
  Under `--ast-frontend clang` the attach never performed a second parse to
  begin with (the primary dump hands it a tree through the in-process memo),
  so that backend is unchanged. Evidence is identical either way: the same
  readers run over the same nodes in the same order, verified by comparing
  the whole projection against the non-streaming one over real clang output.

### Fixed

- **A corrupt clang AST cache entry no longer aborts a dump.** The
  header-graph readers walk a tree whose shape they trust, so an entry that
  decoded as JSON but held the wrong shape (an `inner` that is a number
  rather than a list) raised `TypeError` out of the projection step and
  failed the whole dump, where ADR-028 D3 requires degrading to the
  declaration-only graph. Both projection paths now contain it.

### Changed

- **Streaming applies only above a document-size threshold**
  (`ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB`, default 32 MiB). It trades CPU
  for memory, which is only worth it at scale: a small library's AST
  document is a couple of MiB, where the whole tree costs nothing worth
  avoiding and streaming was measurably 44–71% slower in attach wall time
  for no benefit.
- **A truncated clang AST document is now rejected rather than silently
  read short.** The streaming scanner accepted a document cut off after a
  complete element, or one whose root never closed, returning the elements
  it had managed to read and reporting success — where `json.loads`
  rejects all of them. A half-written cache file is the likeliest
  corruption in practice, and the result was a short projection: missing
  graph edges, hence missing findings, with no error anywhere.
