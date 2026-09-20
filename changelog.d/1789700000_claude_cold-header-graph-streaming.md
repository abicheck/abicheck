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
