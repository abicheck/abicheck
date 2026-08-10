### Added

- **Compressed snapshot storage (ADR-059)** — `compare`, `scan --against`,
  and the Python API (`load_snapshot`) now transparently *read*
  gzip- and zstd-compressed snapshots (`*.abicheck.json.gz`/`.zst`,
  detected by magic bytes, not just filename) alongside plain JSON.
  `abicheck dump` (and the Python API's `write_snapshot`) can now
  *produce* one: `dump` gained a `--compression [auto|none|gzip|zstd]`
  option (default `auto`, inferred from `-o/--output`'s suffix) and prints
  a storage summary (`Storage: zstd, 19,169 -> 3,191 bytes (16.6%)`) after
  writing.
  `zstandard` is now a core dependency (previously scoped to the
  `validation` extra). The internal snapshot cache
  (`abicheck/snapshot_cache.py`) now stores new entries zstd-compressed,
  with transparent fallback to legacy plain-JSON entries. `actions/baseline`,
  the root composite Action's `dump` mode, `abi-baseline: latest-release`/
  `<tag>` release-asset auto-fetch, and `publish-baseline.yml`/
  `update-main-baseline.yml` all gained a `snapshot-compression` input (or,
  for `resolve-baseline`, needed no change at all — already transparent).
  See ADR-059 for the full storage-envelope model, determinism/atomicity/
  decompression-limit guarantees, and what remains deferred (baseline-set
  manifest v2, a deterministic `.tar.zst` release packager, and the wider
  documentation sweep).

### Performance

- **`dump`'s write path no longer re-parses/re-serializes the whole
  snapshot** — the previous pipeline built a full JSON string via
  `snapshot_to_json()`, then `fold_dump_provenance_into_json()` re-parsed
  and re-serialized that entire string a second time just to attach the
  `dump_provenance` key. The write path now builds one payload dict,
  folds `dump_provenance` into it in place
  (`fold_dump_provenance_into_dict`), and encodes to JSON exactly once —
  removing a full second parse/encode pass, most impactful on the
  100+ MB snapshots real large libraries (e.g. oneDAL) produce.
