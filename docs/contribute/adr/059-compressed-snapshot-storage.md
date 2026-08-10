# ADR-059: Compressed Snapshot Storage Envelope

**Date:** 2026-08-10
**Status:** Accepted — partially implemented (core snapshot I/O, `dump` CLI,
`compare`/`scan --against`/Python API/service layer, and the internal
snapshot cache are implemented and tested; baseline-set manifest v2, the
`actions/baseline`/root composite Action/`resolve-baseline`/publish-workflow
changes, and the wider documentation sweep described below are deferred —
see "What this ADR does not (yet) close").
**Decision maker:** Nikolay Petrov

---

## Context

A real-world audit of two oneDAL libraries found:

| Library | Raw snapshot | Functions | Types |
|---|---|---|---|
| `daal` | 149.45 MB | 23,006 | 1,044 |
| `oneapi::dal` | 114.98 MB | 5,976 | 912 |

Each snapshot's embedded L5 header-declaration graph (`build_source`'s
inline pack, see `abicheck/buildsource/CLAUDE.md`) accounts for roughly
57–59 MB, ~38k graph nodes, and ~92k graph edges. A release bundling both
libraries as raw JSON is ~264 MB; gzipped, ~7.7 MB (daal ~3.4 MB,
`oneapi::dal` ~4.3 MB). The content is highly repetitive — JSON keys,
namespace paths, header paths, producer strings, and graph facts recur
constantly — so this is a storage-format problem, not an evidence-bloat
problem: the fix is compressed storage, not deleting graph/evidence content
(explicitly out of scope — see "Non-goals").

Before this ADR, `abicheck/serialization.py`'s `load_snapshot`/
`save_snapshot` always did plain `open(..., encoding="utf-8")` — no
gzip/zstd support anywhere in the snapshot I/O path (confirmed by a full
producer/consumer audit — see "Audit" below). `zstandard` was already a
dependency, but scoped to the `validation` extra and used only by the
conda-forge `.conda`-archive test harness, never for snapshot I/O.

### Audit (producers/consumers of snapshot I/O, before this ADR)

| Call site | plain | gzip | zstd | notes |
|---|---|---|---|---|
| `serialization.load_snapshot`/`save_snapshot` | yes | no | no | the only snapshot file I/O choke point |
| `service.resolve_input` (`sniff_text_format`) | yes | no | no | magic-byte-blind; a compressed file sniffed as `"unknown"` |
| `cli_resolve._sniff_text_format` | yes | no | no | second copy of the same heuristic (`compare`'s per-side metadata classification) |
| `snapshot_cache.py` (`lookup_key`/`store_key`) | yes | no | no | up to 100 raw JSON entries, `<key>.json` |
| `cli.py` `dump` write path | yes | no | no | `snapshot_to_json()` → `fold_dump_provenance_into_json()` (a **second** full `json.loads`/`json.dumps` round trip) → `Path.write_text` |
| `buildsource/baseline_set.py` (`_snapshot_digest_issue`) | yes | no | no | reads raw dict directly (bypasses `AbiSnapshot`) for content-hash validation |
| `actions/baseline/build_manifest.py` | yes | no | no | reads `.abicheck.json` as a raw dict by design (schema-decoupled) |
| `action/run.sh` (`abi-baseline: latest-release`) | yes | no | no | globs only `*.abicheck.json` |
| `pyproject.toml` | n/a | n/a | `validation` extra only | `zstandard` not reachable from snapshot I/O at all |

## Decision

### 1. Logical schema vs. storage envelope

The **logical snapshot** stays exactly what it has always been: a JSON
object with a top-level `schema_version` (`serialization.SCHEMA_VERSION`,
currently 20), produced by `snapshot_to_dict`/consumed by
`snapshot_from_dict`. Compression is a **storage/transport envelope**
around that same JSON payload, decided per-file, and never a new snapshot
schema:

- it does not change `schema_version`, the extraction contract
  (`AbiSnapshot.contract`), profile/scope fingerprints, evidence depth,
  `build_source`, graph nodes/edges, verdict, or field ordering;
- a decoded compressed snapshot's bytes are **byte-identical** to what the
  plain writer produces for the same logical snapshot (P0 deliberately
  keeps compression a pure envelope, not a new compact JSON dialect —
  verified by `test_decompressed_bytes_match_plain_writer_bytes`);
- `snapshot_from_dict(json.loads(decoded_bytes))` yields the identical
  `AbiSnapshot` regardless of which encoding produced `decoded_bytes`.

Vocabulary used consistently across code/docs: *logical snapshot* /
*decoded snapshot* (the JSON payload), *stored snapshot* / *storage
envelope* (the on-disk bytes, possibly compressed), *logical content hash*
(hash of the stable decoded dict — `baseline_set.compute_snapshot_content_
hash`, unaffected by storage encoding), *stored-file hash* (sha256 of the
literal on-disk bytes, encoding-dependent), *decoded size*, *stored size*,
*compression algorithm*.

A single `.json.zst` file is a **snapshot storage envelope**, never called
an "archive" in code or docs — a baseline-**set** `.tar.zst` (multiple
libraries/binaries bundled) is the thing this repo calls an archive/
container; the two are architecturally distinct and must not be conflated.

### 2. Supported algorithms

One project-wide enum, `abicheck.snapshot_io.SnapshotCompression`:

```
auto | none | gzip | zstd
```

- **plain** (`none`) — `*.abicheck.json` / `*.abi.json` / any other `.json`.
  Best for debugging and small, Git-reviewable snapshots.
- **gzip** — `*.abicheck.json.gz` / `*.abi.json.gz`. Universally available,
  good for interoperability with tooling that only speaks gzip.
- **zstd** — `*.abicheck.json.zst` / `*.abi.json.zst`. **Preferred** for
  baseline/release/cache storage: better ratio and speed than gzip at the
  levels this project uses (see "Compression levels" below).

`bz2`/`xz`/`lz4` are explicitly out of scope for this pass (P0) — not
because they're bad, but because supporting a fourth codec doubles the
detection/determinism/limit-testing surface for no acceptance-criteria gain.

### 3. Detection: magic bytes, not suffix

`abicheck.snapshot_io.detect_snapshot_compression`/
`detect_compression_from_bytes` classify a stored snapshot from its first 4
bytes — `1f 8b` (gzip), `28 b5 2f fd` (zstd), else plain — never from the
filename alone. `suffix_compression`/`resolve_write_compression` separately
answer what a *canonical suffix* implies, and the two are cross-checked on
read: a canonical-suffix file whose magic bytes disagree with the suffix is
a hard `SnapshotError` (never a silent guess in either direction).
`bounded_decoded_prefix` reads a small bounded decoded prefix (never a full
decompression) so input classification (`service.sniff_text_format`,
`cli_resolve._sniff_text_format`) can tell a compressed *snapshot* (decoded
prefix starts with `{`) from an unrelated compressed *archive* (a
baseline-set `.tar.zst`, which doesn't) without misrouting either one.

### 4. Canonical snapshot I/O layer

`abicheck/snapshot_io.py` is the one module that knows how a snapshot is
stored — a dependency-free leaf module (no import of the rest of
`abicheck`) so it can be imported from `serialization.py`,
`snapshot_cache.py`, and CLI/service code without growing the existing
CLI-registration import-cycle SCC (`IMPORT_CYCLE_ALLOWLIST`). It exposes:

- `SnapshotCompression`, `SnapshotWriteResult` (path, compression, decoded/
  stored size, stored sha256, `.ratio`), `SnapshotStorageInfo`;
- `detect_snapshot_compression`, `detect_compression_from_bytes`,
  `suffix_compression`, `resolve_write_compression`,
  `bounded_decoded_prefix`, `read_snapshot_storage_info`;
- `read_snapshot_bytes`/`read_snapshot_text` (transparent decode, with a
  decoded-size limit — see "Decompression limits");
- `write_snapshot_bytes`/`write_snapshot_text` (atomic, deterministic,
  compression-aware write).

`abicheck/serialization.py` stays the public compatibility surface:
`load_snapshot(path)` transparently reads plain/gzip/zstd; `save_snapshot
(snap, path, *, compression="auto")` keeps its historical two-positional-
argument calling convention (compression is keyword-only, defaulting to
`"auto"`, so every existing `save_snapshot(snap, path)` call is unchanged);
`write_snapshot(snap, path, *, compression="auto", zstd_level=None) ->
SnapshotWriteResult` is the new richer entry point for callers that want
the write summary. `snapshot_to_json()` remains the in-memory string helper
for callers/tests that want a JSON string without touching a file.

### 5. `zstandard` promoted to a core dependency

`.json.zst` is a first-class format, so it must work after a plain `pip
install abicheck` on every supported Python (3.10–3.14 have no stdlib
zstd) and OS, without depending on a system `zstd` binary. `zstandard` moved
from the `validation` extra into `[project.dependencies]`; the
`validation` extra is kept (now empty) rather than removed, so an existing
`pip install "abicheck[validation]"` invocation doesn't fail on an unknown
extra.

### 6. Deterministic compression

Byte-for-byte reproducible for identical logical payload + settings, on
Linux/macOS/Windows:

- **gzip**: fixed `compresslevel=9`, `mtime=0`, no embedded filename
  (`gzip.compress(data, compresslevel=9, mtime=0)` — the stdlib module
  never writes a filename when compressing from bytes rather than a real
  file). Verified: `test_gzip_header_has_no_embedded_filename_or_mtime`,
  `test_deterministic_gzip_bytes`.
- **zstd**: one `zstandard.ZstdCompressor` backend everywhere (no system
  `zstd`), `write_checksum=False`, `write_content_size=True`, and two
  fixed, **project-owned** levels rather than a user knob (a P0
  requirement — no `--compression-level` flag, to avoid a second profile/
  storage drift axis):

  | Use | Level | Why |
  |---|---|---|
  | Baseline/release (`ZSTD_LEVEL_BASELINE`) | 19 | written rarely (a CI publish job), read often — take the slow/best-ratio end |
  | Internal cache (`ZSTD_LEVEL_CACHE`) | 3 | written on nearly every `dump`/`compare` invocation — take the fast end |

  Chosen from a measured trade-off on an ~18.5 MB graph-heavy synthetic
  snapshot (38k-node/92k-edge shape, matching the real oneDAL L5 graph
  section's scale):

  | Level | Stored size | Ratio | Compress time |
  |---|---|---|---|
  | 3 | 2.90 MB | 15.6% | 0.07s |
  | 6 | 2.64 MB | 14.2% | 0.18s |
  | 10 | 2.27 MB | 12.3% | 0.37s |
  | 15 | 1.93 MB | 10.4% | 1.85s |
  | 19 | 1.40 MB | 7.6% | 12.96s |
  | gzip -9 | 3.28 MB | 17.7% | 0.80s |

  Extrapolated to a real ~150 MB snapshot, level 19 costs roughly
  100–110s of compression time — acceptable for a periodic baseline-
  publish CI job, not for a per-invocation cache write.

P0 does not attempt a custom compact JSON dialect alongside compression —
see "Non-goals".

### 7. Atomic, safe writes

Every canonical write (`write_snapshot_bytes`/`write_snapshot_text`, and
therefore every writer built on it — `dump`, the snapshot cache, `save_
snapshot`/`write_snapshot`) is atomic: a temp file in the same directory
(`tempfile.mkstemp`), full payload write, `flush()`, best-effort `fsync()`,
`os.replace()` onto the final path, with the temp file removed on any
failure and the destination left untouched. Verified:
`test_atomic_write_leaves_no_temp_file`, `test_failed_write_preserves_
existing_destination` (injects an `os.replace` failure and asserts the
pre-existing destination bytes are unchanged and no stray temp file is
left).

The `dump` write path (see "`dump` pipeline" below) never writes an
intermediate raw snapshot next to the compressed one — the compressor
consumes the already-built JSON text directly.

### 8. Decompression limits (bomb defence)

`DEFAULT_MAX_DECODED_BYTES = 1 GiB` — comfortably above the real oneDAL
snapshots' ~150 MB decoded size, private-override-only via
`_ABICHECK_SNAPSHOT_MAX_DECODED_BYTES` for tests (no public CLI flag, per
the "no new knobs without product need" rule). Both gzip and zstd decode in
bounded chunks, raising `SnapshotError` the moment cumulative decoded bytes
exceed the limit — the same fail-closed behavior either way. zstd
additionally bounds the decompressor's window size (`max_window_size = 1 <<
31`, a 2 GiB ceiling) independent of the decoded-byte limit, so a hostile
frame can't force an oversized window allocation regardless of what it
claims to produce. A frame that decompresses cleanly but short of its own
declared `content_size` (a truncation shape that a naive streaming read
loop can silently swallow as "just fewer bytes than expected" — confirmed
empirically) is cross-checked against that declared size and raised as
corrupt, not accepted as a short read.

### 9. Logical vs. stored digests; `dump` pipeline

`dump`'s write path used to be:

```
AbiSnapshot -> snapshot_to_json() [full JSON string]
            -> fold_dump_provenance_into_json() [json.loads + mutate + json.dumps -- a SECOND full parse/encode]
            -> Path.write_text()
```

Now:

```
AbiSnapshot -> snapshot_to_dict() [one payload dict]
            -> fold_dump_provenance_into_dict() [mutate the dict in place]
            -> json.dumps() [one encode]
            -> write_snapshot_text() [atomic, optionally compressed]
```

`fold_dump_provenance_into_dict()` (new) does the same augmentation as
before directly on the dict; `fold_dump_provenance_into_json()` (the
former sole entry point, `dump_provenance` is JSON-only payload
augmentation, not an `AbiSnapshot` field — unchanged by this ADR) is now a
thin backward-compatible wrapper over it for existing callers/tests. `dump`
prints a compact storage summary to stderr (never stdout, which stays pure
JSON when no `-o/--output` is given):

```
Snapshot written to foo.abicheck.json.zst
Storage: zstd, 19,169 -> 3,191 bytes (16.6%)
Resolved evidence depth: headers
```

`--compression` (`auto` default) on `abicheck dump` resolves from
`-o/--output`'s suffix; an explicit value that contradicts a canonical
output suffix is a hard `UsageError`, never a silent rename/override.
`--compression <non-none>` with no `-o/--output` is also a hard
`UsageError` (stdout always stays plain JSON). `dump --dry-run` shows the
resolved compression (and that no file will be created) without invoking
the compressor — moot in practice today since `--dry-run` and `-o/--output`
are already mutually exclusive at the CLI level, but implemented for
forward compatibility and exercised via unit tests on the underlying
resolver.

Not attempted in this pass, and explicitly deferred (see "Non-goals"):
replacing `dataclasses.asdict()` (`snapshot_to_dict`'s own base) with a
hand-rolled incremental/streaming JSON encoder. The two full-payload passes
this ADR removes (`snapshot_to_json` + `fold_dump_provenance_into_json`'s
re-parse) were the concrete, measured problem; `asdict()` itself was not
separately profiled as a bottleneck in this pass.

### 10. Snapshot cache (`snapshot_cache.py`)

New entries are written zstd-compressed at `ZSTD_LEVEL_CACHE`
(`<key>.json.zst`), through the canonical atomic writer. `lookup_key`
checks the compressed entry first, then falls back to a legacy plain
`<key>.json` entry so an upgrade doesn't discard a warm cache wholesale.
`store_key` removes a stale legacy plain entry for the same key after
writing the compressed one, so a lookup never prefers stale content over a
freshly stored entry. `_evict_if_needed`'s LRU glob covers both suffixes.
A corrupt/truncated compressed cache entry is a cache miss (the existing
"any read problem here is cache-safe, never a crash" stance), not a
caller-visible failure. `_SNAPSHOT_CACHE_VERSION` is **not** bumped: the
cache key is a function of the dump *inputs*, not the storage encoding, and
existing entries stay valid and readable — only new writes changed shape.

### 11. What this ADR does not (yet) close

Deferred, tracked as follow-up work rather than attempted as a drive-by
extension of this pass (each is a separately-scoped project of its own):

- **Baseline-set manifest v2** (compressed member paths, a `storage` block
  per artifact recording compression/stored-sha256/stored/decoded size
  alongside the existing logical `sha256`, v1 backward compatibility).
- **`actions/baseline`** (a `snapshot-compression` input defaulting to
  `zstd`, stale-file cleanup across all three suffixes,
  `build_manifest.py` reading through canonical snapshot I/O instead of a
  direct `open()`/`json.load()`).
- **The root composite Action** (`snapshot-compression` input, a
  `.abicheck.json.zst` default `dump`-mode output path, `snapshot-path`/
  `snapshot-compression`/size outputs, `abi-baseline: latest-release`
  recognizing `.gz`/`.zst` variants and rejecting a raw+compressed
  ambiguity).
- **`actions/resolve-baseline`** (manifest v2 parsing, compressed-member
  stored/logical digest validation, no raw-temp-file extraction).
- **`publish-baseline.yml`/`update-main-baseline.yml`** (a
  `snapshot-compression` workflow input, a shared deterministic
  Python/`zstandard` `.tar.zst` packager to replace `tar --zstd`, which
  does not guarantee deterministic tar metadata across runners).
- **The wider documentation sweep** listed in the originating task
  (`docs/reference/snapshot-format.md`, `docs/use/*baseline*`,
  `docs/use/github-action*.md`, `docs/reference/*-baseline.md`,
  `docs/start/*`, the upgrade guide, `README.md`, `mkdocs.yml` nav) beyond
  this ADR and ADR-015's cross-reference below.
- **`BuildSourcePack` externalization/deduplication** (an `inline |
  referenced | auto` packaging mode, content-addressed dedup of a shared
  pack between `daal`/`oneapi::dal`-shaped sibling libraries) — the
  architectural question is real (see the originating task's "internal/
  out-of-band buildsource packs" section) but is its own scoped design, not
  something this ADR's storage-envelope change should half-implement.
- **Graph wire-schema compaction** — the L5 node/edge JSON shape itself is
  unchanged; this ADR closes the storage problem it causes without
  touching its representation, deliberately (a schema change needs its own
  versioned design and backward-compat tests, per this repo's existing
  ADR-046/048 graph-identity conventions).

None of the above blocks what *is* implemented: `dump`, `compare`, `scan
--against`, the typed Python API, and the internal snapshot cache all
already transparently read/write plain, gzip, and zstd snapshots today —
see the acceptance table in the PR/CHANGELOG entry for this ADR.

## Non-goals (explicit)

- **DWARF/scan extraction memory (RSS)** is out of scope. This ADR touches
  only the serialization/write stage (removing redundant parse/encode
  passes, adding atomic + streaming-friendly compression) — it does not
  touch `DwarfSession`, DIE caches, basic/advanced DWARF extraction, scan
  memory limits, runner swap, scan/baseline timeout budgets, extraction
  concurrency, or reachability/filtering algorithms. A separate,
  concurrent effort owns DWARF/scan memory work; this ADR does not claim
  to solve it and did not modify any of the files that effort owns.
- No new compact/binary JSON dialect (see "Compression levels" above).
- No `bz2`/`xz`/`lz4` support.
- No user-facing `--compression-level` flag.
- No new `snapshot inspect`-shaped root CLI command (the storage summary
  `dump` already prints, plus the typed `SnapshotWriteResult`/
  `SnapshotStorageInfo` Python API, cover the needed observability without
  a new command clearing ADR-054's admission bar).

## Backward compatibility

- Every pre-existing plain `.abi.json`/`.abicheck.json` snapshot loads
  unchanged (`test_pre_compression_fixture_still_loads`).
- `save_snapshot(snap, path)` — the historical two-positional-argument
  call — is unchanged; `compression` is a new keyword-only parameter
  defaulting to `"auto"` (`test_save_snapshot_legacy_positional_signature_
  unchanged`).
- A pre-this-ADR reader (an already-released abicheck) has no code path
  that recognizes gzip/zstd magic bytes at all and will fail attempting to
  `json.loads()` a compressed file — this is the expected, documented
  migration boundary (see the CHANGELOG/migration-guide entry for this
  ADR): a compressed snapshot requires an abicheck build containing this
  ADR's reader to open. No `schema_version` bump was needed or made, since
  the logical schema is unchanged — see "Logical schema vs. storage
  envelope" above.

## Related

- ADR-015 (Snapshot Serialization and Schema Versioning) — this ADR is
  additive to it: the schema-versioning contract ADR-015 established is
  unchanged; this ADR adds a storage envelope around the same schema.
- ADR-028/031 (single-artifact UX, inline `BuildSourcePack`) — the L5
  graph section this ADR's compression benefits most is the one those
  ADRs chose to embed inline; see "What this ADR does not (yet) close"
  for the deferred externalization question.
- ADR-050 (comparability contract) — unaffected: `ExtractionContract`
  fingerprints are computed from decoded snapshot content, identical
  across encodings.
