### Performance

- `storage.canonical.canonical_form` -- the hot leaf of every snapshot-cache load and write -- now dispatches on exact `dict`/`list`/`tuple`/scalar types before any ABC `isinstance` check, and skips re-sorting a mapping whose keys already ascend (every document read back from a canonical store). Output is unchanged.
