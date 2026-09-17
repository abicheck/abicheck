### Changed

- **Performance (`surface_graph.py`): dropped the eager inverse-reachability
  closure.** `SurfaceGraph` materialised a `reached_by` mapping (type name ->
  the public roots that transitively reach it) at construction, by running the
  full `reachable_types()` walk once per public root. An audit found it had no
  production consumer anywhere in the tree — its only readers were three
  assertions in `tests/test_surface_graph.py` and a code sketch in ADR-027 —
  while costing O(roots x types) time and memory on every graph build.
  Building the graph for a snapshot with 1,000 public roots over a 1,000-type
  shared chain dropped from 32.13 MiB live / 63.74 MiB peak / 2.559 s to
  0.64 MiB / 0.79 MiB / 0.026 s, and the growth is linear rather than
  quadratic. The relation itself is still derivable in two lines from
  `public_roots()` and `reachable_types()`, and the semantic claim its test
  guarded is asserted that way now rather than deleted.

- **Performance (`storage/snapshot_encode.py`): one encoding walk instead of
  two.** `snapshot_to_dict()` built the encoded tree with
  `dataclasses.asdict()` and then built a *second* complete tree from it with
  `_sets_to_lists()`, so the encoder's transient peak was roughly twice the
  encoded document before anything had been written. The two passes are fused
  into a single explicit walk (`_encode_value`) that performs the
  dataclass-to-dict projection and the set-to-sorted-list conversion together.
  Encoding a 20,000-function snapshot now peaks at 138.58 MiB instead of
  240.68 MiB (-42%) and runs ~18% faster. Output is byte-identical: validated
  against the previous implementation as an independent oracle over empty,
  symbol-only and L2 snapshots and over a real 42 MB CastXML extraction,
  comparing the dict, the canonical JSON text and the content digest.

### Fixed

- **`snapshot_to_dict()` no longer mutates the snapshot it is encoding.** To
  keep `asdict()` away from five fields (the three lazy lookup caches, and the
  `surface_graph`/`semantic_ir` fields owned by dedicated codecs), the previous
  implementation *assigned `None` to them on the caller's live snapshot*, ran
  `asdict()`, and restored them in a `finally`. Anything else holding that
  snapshot for the duration of the encode — a second serialization, a
  concurrent reader — observed it with its evidence silently missing. The
  fields are now skipped at the point of the walk, so the function is pure with
  respect to its argument. Covered by a deterministic regression test that
  observes the snapshot *from inside* the walk (no threads, no timing): a
  before/after comparison around the call could never have caught this, because
  the `finally` restored every field before returning.

- **Performance (`storage/bundle_facts_archive.py`): bundle archive writing no
  longer retains every encoded payload.** The writer accumulated each distinct
  encoded snapshot's bytes in an in-memory `unique_payloads` map and only began
  emitting archive members once the whole bundle had been serialized, so
  resident memory grew with the bundle's member count on top of the model
  objects the caller still held. Payloads are now spooled to a temporary file
  as they are produced and only `{hash: (offset, length)}` stays resident —
  none of the pre-write validation ever needed the bytes, only each blob's
  length and how many library names reference it. Write peak for a bundle of
  fixed per-member size is now flat at ~142 MiB from 6 to 18 members, where it
  previously grew 170.42 -> 198.29 -> 254.97 MiB. Archives are byte-identical
  (same `stored_sha256` at every size): members are still emitted sorted by
  content hash, which is what the spool preserves, and validation still
  completes before the real archive's own temp file is opened, so a refused
  write publishes nothing.
