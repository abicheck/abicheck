### Performance

- Loading and writing a stored snapshot (every snapshot-cache hit and store, every `compare` against a stored baseline) no longer repeats work whose result it already has. On a 238 MB header-AST snapshot `load_snapshot` drops from 19.4 s to ~6.9 s and a snapshot write from 26.6 s to ~12.3 s; the loaded snapshot and the written bytes are identical to before.
  - The `semantic_ir` section is decoded once on load, instead of decoded, re-encoded and decoded again, and packaged from `snapshot_to_dict`'s own encoding on write, instead of decoded and re-encoded through a frozen DTO.
  - A section payload that `load_snapshot` parsed itself, and that is already canonical, is used as is rather than deep-copied through `canonical_form`; anything not canonical (a hand-edited document) is still normalized.
  - The write path's encoder emits the canonical shape directly (dataclass fields in key order, sequences as lists), so canonicalizing the document reuses it (`canonical_form_shared`) instead of rebuilding nearly every dict.
  - Parsing, decoding and encoding run with the cyclic garbage collector paused (`acyclic_json.gc_paused`): they build millions of containers that are not garbage until the operation ends.
  - The load-time closure-marker normalization and renumbering walk the snapshot once instead of twice, and not at all when the document text contains no marker.
  - `canonical_form` dispatches on exact `dict`/`list`/`tuple`/scalar types before any ABC `isinstance` check and skips re-sorting a mapping whose keys already ascend.
- `deadline.run_bounded` no longer holds the process-wide process-group lock across `subprocess.Popen`, which serialized every subprocess spawn in the interpreter. The SIGTERM cleanup handler instead stops new spawns and waits for in-flight ones to register before killing the tracked groups, so the no-orphan guarantee is unchanged. A spawn attempted while the handler runs now raises `deadline.ProcessTerminating`.
