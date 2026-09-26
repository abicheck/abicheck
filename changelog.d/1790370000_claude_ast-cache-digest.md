### Changed

- L2 header-AST cache entries (clang JSON and castxml XML) now carry a
  `<entry>.sha256` content digest (`storage/cache_integrity.py`). An entry
  that no longer matches its digest -- truncated, bit-rotted on a shared
  cache volume, or edited by hand -- is evicted and re-parsed instead of
  being consumed silently. Entries written before this change are trusted on
  first use and recorded then. This detects corruption, not tampering by
  someone who can also rewrite the sidecar.
