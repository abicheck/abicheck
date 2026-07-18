<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- The whole-snapshot dump cache's key hashed the raw `--ast-frontend`
  string as passed (e.g. `"auto"`), not the actual resolved backend. Since
  `"auto"` consults `ABICHECK_AST_FRONTEND` at dump time, an env-pinned
  `hybrid`/`clang` request and an unpinned `auto` request produced the
  identical cache key — on this on-disk, cross-invocation cache, a later
  run with the env var in a different state could silently reuse a
  snapshot from the wrong producer instead of ever calling the real dump.
  The cache key now hashes the resolved backend.
