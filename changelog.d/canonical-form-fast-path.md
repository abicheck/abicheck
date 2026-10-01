### Performance

- Loading a stored snapshot (every snapshot-cache hit, every `compare` against a stored baseline) no longer repeats work whose result it already has. On a 238 MB header-AST snapshot `load_snapshot` drops from 19.4 s to 9.6 s, and writing one (every snapshot-cache store) from 26.6 s to 17.6 s; the loaded snapshot and the written bytes are identical to before.
  - The `semantic_ir` section is decoded once on load, instead of decoded, re-encoded and decoded again, and packaged from `snapshot_to_dict`'s own encoding on write, instead of decoded and re-encoded through a frozen DTO.
  - A section payload `load_snapshot` parsed itself, and that is already canonical, is used as is rather than deep-copied through `canonical_form`; anything not canonical (a hand-edited document) is still normalized.
  - The load-time closure-marker normalization and renumbering walk the snapshot once instead of twice, and not at all when the document text contains no marker.
  - `canonical_form` dispatches on exact `dict`/`list`/`tuple`/scalar types before any ABC `isinstance` check and skips re-sorting a mapping whose keys already ascend.
- `deadline.run_bounded` no longer holds the process-wide process-group lock across `subprocess.Popen`, which serialized every subprocess spawn in the interpreter. The SIGTERM cleanup handler instead stops new spawns and waits for in-flight ones to register before killing the tracked groups, so the no-orphan guarantee is unchanged. A spawn attempted while the handler runs now raises `deadline.ProcessTerminating`.
