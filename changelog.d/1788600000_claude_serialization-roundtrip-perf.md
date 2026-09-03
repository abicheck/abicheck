### Performance

- **Cut redundant `canonical_form` traversals in the storage object store.**
  `abicheck.storage.package.InMemoryObjectStore.put()`/`get()` were each
  re-running a full `canonical_form` pass over content that was already
  canonical (once to normalize for storage, again inside `semantic_digest`
  to hash it, and a third time on `get()` just to hand back an isolated
  copy) — pure duplicated work with no effect on the result. `put()`/`get()`
  now go through the new `semantic_digest_of_canonical_form`/
  `copy_of_canonical_form` helpers in `abicheck.storage.canonical`, which
  skip the redundant re-normalization. `canonical_form` itself also gets a
  fast path for the overwhelmingly common concrete `dict`/`list` shapes
  (ahead of the general `Mapping`/`Sequence` `isinstance` checks), and
  `_has_surrogate_pair`'s per-string scan now uses a compiled regex instead
  of a Python-level loop. Snapshot serialization round-trip time for a
  1000-function/500-type snapshot dropped from ~2.6s to under 1.5s, with no
  change to stored content or digests.
