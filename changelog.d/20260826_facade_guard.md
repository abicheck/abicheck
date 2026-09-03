### Fixed

- The `abicheck.cli` compatibility facade now rejects assignment of a name it
  resolves lazily, instead of silently freezing a stale reference for the rest
  of the process. Assigning one (a `monkeypatch.setattr` against the facade
  suffices — undo re-assigns the value it read) shadowed the lazy lookup, so
  every later caller read the frozen original and every later patch of the true
  owner was ignored. Three call sites that reached moved names through the
  facade now bind their owner directly.
