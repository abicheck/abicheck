### Fixed

- **Reuse warm L2 header acquisitions throughout one request** — clang and CastXML disk-cache hits now enter the same request-local singleflight operation as compiler misses, so repeated members and header-graph consumers reuse the decoded AST and its resolved frontend metadata.
