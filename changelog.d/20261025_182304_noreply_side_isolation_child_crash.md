### Fixed

- **`compare` no longer crashes in a forked side child with
  `'object' object has no attribute 'get'`** — with `--build-info`, the
  header-graph parse that runs beside the primary dump and the castxml
  vector-type join can ask for the same clang AST. When the header-graph
  parse got there first and used a stored projection instead of the tree,
  the request's shared AST table handed its placeholder to the vector join as
  if it were the tree. A consumer served that placeholder now takes its own
  projection from the cache entry, or, if it needs the tree, replaces the
  placeholder with one, so the header-graph parses of a release's members
  still share one acquisition. An exception forwarded from a side child now carries the
  child's traceback as a note, and a failed AST producer keeps its own
  frames, so the next failure on this path can be traced to where it was
  raised.
