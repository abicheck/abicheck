### Performance

- **`compare` no longer serializes each snapshot six times.**
  `serialization.snapshot_content_digest` is a pure function of a
  snapshot's persisted content, but its two consumers
  (`workflows.gate.snapshot_identity_digest` and
  `storage.snapshot_encode.same_persisted_content`, which serializes both
  sides) are reached three times in one comparison — `checker.compare`'s
  own assurance attach, the front end's re-attach with the real evidence
  pack, and the same-binary-compared digest fallback. On a real oneAPI
  corpus that accounted for 404s of a 620s self-compare and a ~1.7-1.8x
  regression across every graph-shaped comparison. The digest is now
  memoized per snapshot for the duration of one comparison
  (`storage.snapshot_digest_cache.digest_scope`, entered by
  `cli_compare_helpers.run_compare` and
  `service_compare_pipeline.classify_compare_pair`), and
  `same_persisted_content` short-circuits on object identity. The digest
  string is unchanged — it appears in report output and in cache keys —
  and no finding, verdict, or exit code is affected.
