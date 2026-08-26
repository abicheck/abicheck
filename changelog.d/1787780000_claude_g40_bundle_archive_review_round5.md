### Fixed

- **G40 bundle archive: four more Codex review findings, all real, all fixed.**
  (1) `write_bundle_facts_archive`'s library-name-count cap was checked
  *after* the loop that serializes every entry -- many names referencing
  one large shared snapshot could serialize that same payload once per
  name (possibly terabytes of work) before ever getting a chance to be
  rejected. Now checked before the loop, from `facts.per_library_
  snapshots` directly. (2) `open_regular_file_for_format_sniff`'s 4-byte
  prefix read had no error handling -- a failure reading it (e.g. EIO on
  a network filesystem) leaked the fd and propagated a raw `OSError`
  instead of this module's `SnapshotError` contract. Now wrapped, closing
  the fd on failure. (3) `BundleArchiveWriter.close()`'s chown/chmod/hash
  steps operated on `self._tmp_path` by name, not on the fd held open
  since exclusive creation -- a hostile actor sharing a non-sticky,
  writable directory could substitute a file/symlink at that path between
  creation and these later path-based reopens, so chown/chmod would
  follow the substitution and the hash would verify the attacker's
  content instead of what was actually written. Now uses `os.fchown`/
  `os.fchmod` on the writer's own fd, and reads back through a `os.dup()`
  of that fd (the temp file is now opened `O_RDWR` instead of `O_WRONLY`
  to make this possible) rather than reopening by path. The final
  `os.replace()` publish step remains inherently path-based -- a known,
  documented residual gap (no portable fd-scoped rename in stdlib `os`) --
  but the fix still closes the silent-MITM shape: `stored_sha256` now
  reflects the real content, so a substitution there is detectable rather
  than self-consistently invisible.

  `tests/test_bundle_archive_writer_hardening.py` (new): `BundleArchive
  Writer`'s temp-file/security/metadata hardening tests, split out of
  `tests/test_bundle_archive.py` purely to keep both under the ADR-061
  1200-line test cap after these additions.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) The write-side aggregate-byte cap only summed hashes
  referenced by `library_blobs` -- `manifest_blob`'s own hash, when not
  already shared with any library snapshot (the common case), never
  contributed to that sum, even though the reader's own `_cached_blob()`
  genuinely charges it once on load. A bundle with no (or small) library
  snapshots but an oversized manifest would pass the write-side check and
  then be rejected on load. Now included, charged exactly once (matching
  the reader's own cache-hit-vs-miss behavior). (2) The low-level
  `BundleArchiveWriter.write_manifest()` primitive had no size check at
  all -- a caller using it directly (not through `bundle_facts.write_
  bundle_facts_archive`'s own higher-level preflight) could publish a
  manifest larger than what `read_manifest()` unconditionally accepts.
  Now enforced in the primitive itself too.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `BundleArchiveWriter.put_blob()` never checked the total
  member count against `MAX_ARCHIVE_MEMBERS` -- a direct caller of this
  primitive adding exactly that many distinct blobs, then calling
  `write_manifest()`, would publish an archive one member over what the
  reader's own central-directory preflight accepts, unreadable the
  moment it was written. Now enforced in `put_blob()` itself, with one
  slot reserved for the mandatory manifest member (a byte-identical
  duplicate payload is still a dedup no-op and never counts against the
  cap). (2) `reject_absurd_central_directory()`'s ZIP64-locator path
  seeked to an attacker-controlled 8-byte offset with no bound -- an
  offset like `2**64-1` raises `ValueError` from `f.seek()`, which the
  surrounding `except OSError:` does not catch, so it escaped as a raw
  exception instead of this module's documented `SnapshotError`
  contract. Now bounded against the file's own size before seeking.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `write_bundle_facts_archive`'s aggregate-byte cap held
  every serialized payload in `unique_payloads` before checking the
  limit -- many large distinct snapshots (e.g. 100 x 100 MiB) could
  consume far more memory than the cap itself before the rejection ever
  fired. Now checked incrementally as each distinct payload is added, so
  a bundle whose already-serialized content alone exceeds the cap is
  rejected without serializing the rest. (2) `BundleArchiveWriter._abort()`'s
  single `finally` around `self._zf.close()` skipped the temp-file
  unlink when the *second* close -- `self._tmp_file.close()` -- itself
  raised (ENOSPC/EIO), leaving a potentially large temp archive behind.
  Now both closes are nested in their own `finally` so the unlink always
  runs regardless of which one fails.

- **G40 bundle archive: a real central-directory-cap bypass (P1), plus a
  TOCTOU fix, both from Codex review.** (1) `reject_absurd_central_
  directory()` only checked the EOCD/ZIP64 record's own *declared*
  `total_entries` -- but `zipfile.ZipFile` parses every record it can
  actually find within `cd_size` bytes regardless of what `total_entries`
  claims, so an archive understating that field (e.g. claiming `1` while
  `cd_size`, independently capped but still generously sized, holds
  thousands of real minimal-sized records) bypassed the entry-count cap
  entirely. Now the actual records are counted directly from the bounded
  central-directory bytes, bailing as soon as the real count exceeds the
  limit. (2) `open_regular_file_for_format_sniff()`'s separate `stat()`
  then `open()` left a window where a concurrent replacement could swap a
  regular file for a FIFO in between -- `open()`ing a FIFO for reading
  then blocks until a writer connects, violating this function's own
  contract to return JSON for a non-regular source without reading (or
  blocking on) it. Now opens `O_NONBLOCK` first and `fstat()`s that same
  fd, so the type check and the read refer to one inode with no gap.

  A third finding ("verify zstd frame completion before accepting a
  blob") was investigated and found not to be exploitable in this
  codebase: `read_blob()` already re-hashes the fully decoded payload
  against the content-addressed hash named in the manifest (SHA-256), so
  any truncated/corrupted decode -- confirmed empirically against real
  `zstandard` truncation, which does return partial output without
  raising `ZstdError` -- can never match the recorded hash without
  breaking SHA-256 collision resistance. No code change made for that
  finding; the reasoning is recorded on its own PR review thread.

- **G40 bundle archive: three more Codex review findings, all real, all
  fixed.** (1, P1) Sharing one fd between the central-directory preflight
  and `zipfile.ZipFile` closes a *path-substitution* race but not an
  *in-place content* one -- another writer with access to the same inode
  could still grow the file between the preflight returning and
  `ZipFile`'s own independent, unbounded scan of the (by-then-larger)
  current end of file, reproduced: a one-entry file grown to four entries
  after the check returned still had all four parsed despite a
  three-entry limit. `reject_absurd_central_directory()` now returns the
  file size it validated, and `BundleArchiveReader.__init__` re-`fstat()`s
  immediately before constructing `ZipFile`, rejecting a mismatch -- this
  narrows the window to the two adjacent statements rather than closing
  it outright (a stable, separately-materialized copy of the archive
  bytes would be a much larger change). (2) `write_bundle_facts_archive`'s
  dedup only skipped *adding* an already-seen hash to `unique_payloads` --
  the expensive serialization itself (`snapshot_to_dict`/`json.dumps`)
  still ran once per *name*, even when many names reference the identical
  `AbiSnapshot` object. Now cached per object identity (safe: every
  referenced object stays alive for the whole loop via `facts.
  per_library_snapshots` itself, so `id()` can't be reused mid-loop).
  (3) An explicit `format="archive"` caller reaches `BundleArchiveReader.
  open()`/`__init__` directly, bypassing `open_regular_file_for_format_
  sniff`'s own non-regular-source guard entirely -- a FIFO with no writer
  blocked on `open()` instead of failing cleanly. Now uses the same
  O_NONBLOCK-open + `fstat()`-classify shape as that sniff, raising
  `SnapshotError` for a non-regular source.

- **G40 bundle archive: a real central-directory-cap bypass (P1) in the
  entry-counting fix from the previous round.** `_actual_central_
  directory_entry_count()` seeked to the raw, EOCD/ZIP64-record-declared
  `cd_offset` -- but that field is written relative to the *zip data's
  own start*, not the whole file, so a concatenated/self-extracting
  archive with bytes prepended before the zip structure makes it wrong
  by exactly the prefix length. `zipfile.ZipFile` itself never trusts
  this field directly either: CPython's own `_RealGetContents` derives
  `start_dir = eocd_location - size_cd`, where the claimed offset field
  cancels out of the formula entirely. Reproduced: a prefixed archive
  whose EOCD `total_entries` was patched to 1 counted zero records with
  the old, offset-trusting seek (silently passing) while `ZipFile` itself
  correctly rebased and materialized all 20,001 real `ZipInfo` objects.
  Fixed by deriving the same offset-independent position CPython uses
  (`record_position - cd_size`, where `record_position` is the verified
  location of whichever EOCD/ZIP64 record supplied `cd_size` -- never the
  claimed offset field, which is now not even read). Regression test:
  `test_rejects_understated_entries_even_with_a_prepended_prefix` (a real
  5-record central directory behind a 100-byte prefix, confirmed to pass
  silently against the pre-fix seek and to be correctly rejected against
  the fix).

  `tests/test_bundle_archive_cd_guard.py` (new): the central-directory
  guard and preflight-safety test classes, split out of
  `tests/test_bundle_archive.py` purely to keep both under the ADR-061
  1200-line test cap after these additions.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `BundleArchiveWriter.put_blob()` never checked a payload's
  size against `DEFAULT_MAX_BLOB_BYTES` before compressing and writing
  it -- `read_blob()`'s own default cap is exactly this value, and the
  high-level bundle loader never grants a larger allowance, so a direct
  caller of this primitive could publish an archive its own paired
  reader could never reopen. Now enforced in `put_blob()` itself, same
  reader/writer symmetry as the member-count cap. (2) A central-
  directory member whose `extract_version` exceeds what `zipfile`
  supports makes `zipfile.ZipFile.__init__` raise a bare
  `NotImplementedError` -- neither `BadZipFile` nor `OSError`, so it
  escaped this module's `SnapshotError` contract via the generic
  `except BaseException:` cleanup handler, surfacing a raw traceback
  instead of a clean operational error. Now translated alongside the
  other two exception types.

- **G40 bundle archive: four more Codex review findings, all real, all
  fixed.** (1) The ZIP64-record fallback the previous round's P1 fix
  never handled a *prefixed* ZIP64 archive: the locator's own claimed
  record offset is relative to the zip payload alone, not the whole
  file, so a self-extracting archive with bytes prepended made the raw
  offset wrong -- this guard rejected such an archive outright even
  though `zipfile.ZipFile` (via CPython's own `_EndRecData64`, which
  falls back to the fixed position immediately before the locator when
  the raw offset finds nothing) opens it fine. Now retries at that same
  fallback position before giving up. (2) The incremental aggregate-
  byte check from an earlier round only bounded the *deduped*
  `unique_payloads` total -- distinct `AbiSnapshot` objects that happen
  to serialize identically (not literally the same object, so the
  identity cache misses) still each cost a full, unbounded serialization
  before their shared hash was known, and the duplicate-aware total was
  only checked once, at the very end of the loop. Now `decoded_size_bytes`
  itself (every name's own copy, duplicates included -- already exactly
  equal to what the end-of-loop `reader_charged_bytes` check computes for
  names processed so far) is checked on every iteration. (3) The explicit
  `format="archive"` open path's `os.open()` succeeding but the following
  `os.fstat()` then raising (e.g. EIO) never closed the fd -- only the
  not-regular-file branch did. Now closed on that failure too. (4)
  `read_manifest()`'s `json.loads(raw)` on invalid UTF-8/JSON bytes raised
  a raw `UnicodeDecodeError`/`json.JSONDecodeError`, bypassing this
  module's `SnapshotError` vocabulary the same way corrupt ZIP members and
  zstd payloads are already translated. Now caught and translated too.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `write_manifest()` called `json.dumps(manifest, indent=2)`
  to get an encoded string before checking it against
  `DEFAULT_MAX_MANIFEST_BYTES` -- an oversized manifest was therefore
  fully materialized as one string in memory before this check ever got a
  chance to reject it. Now encoded via `json.JSONEncoder(indent=2).
  iterencode()`, with the running UTF-8 byte count checked chunk by
  chunk, so an oversized manifest is rejected without ever holding the
  whole encoded string. (2) `BundleArchiveReader.__init__`'s
  `from_open_file()` branch rewound the caller-owned fd (`fp.seek(0)`)
  *before* entering the guarded `try`/`except` block that owns closing it
  on failure -- a `seek()` error (e.g. EIO) at that point escaped as a
  raw `OSError` with the fd leaked, instead of this module's
  `SnapshotError` contract. Now the rewind happens inside that same
  guarded block, so a failure there is translated and the fd is closed
  like every other failure path in this constructor.

- **G40 bundle archive: a real central-directory-guard bypass (P1) from
  Codex review, fresh evidence.** `reject_absurd_central_directory()`
  treated an `OSError` from its own `fstat()`/`seek()`/`read()` calls as
  "the check couldn't run, trust `zipfile.ZipFile`" -- returning `None`
  (or, from two later fallbacks, the file's own size) instead of
  rejecting. That is correct for "the EOCD signature itself can't be
  found in the tail" (a genuinely truncated/non-zip file, handled
  separately without raising), but wrong for an I/O failure partway
  through: a transient error (or one an attacker can trigger
  deliberately) at exactly that moment skipped every entry-count/
  central-directory-byte-size bound this preflight exists to enforce,
  letting `zipfile.ZipFile` eagerly parse an unbounded central directory
  right after. Reproduced: a two-entry archive opened past a one-entry
  cap when the guard's own `fstat()` alone raised. Now every one of the
  three `OSError` fallbacks in this function raises `SnapshotError`
  instead -- the function's return type narrows from `int | None` to
  `int` accordingly, and the caller's now-impossible `None` case was
  removed too.

- **G40 bundle archive: one more Codex review finding, real, fixed.**
  `write_bundle_facts_archive`'s own high-level manifest-size preflight
  (checking the assembled `manifest.json` -- `library_blobs` +
  `filesystem_aliases` + `library_filenames` -- against
  `DEFAULT_MAX_MANIFEST_BYTES`) called `json.dumps(container_manifest,
  indent=2)` to get an encoded string, then `.encode("utf-8")` a second,
  separate time inside the error message, before ever checking the
  length -- so a `BundleFacts` with a large `filesystem_aliases`/
  `library_filenames` mapping could fully materialize two independent
  oversized copies before this preflight got a chance to reject it, even
  though `BundleArchiveWriter.write_manifest()`'s own identical check
  (fixed two rounds ago) no longer has this problem. Now checked
  incrementally via `json.JSONEncoder(indent=2).iterencode()`, the same
  fix already applied to the primitive layer.

- **G40 bundle archive: a residual TOCTOU gap investigated, confirmed
  real, and documented rather than fixed (Codex review, fresh evidence,
  same-length reproduction).** The size re-check `BundleArchiveReader.
  __init__` performs immediately before constructing `zipfile.ZipFile`
  catches an in-place *growth* between the preflight and `ZipFile`'s own
  scan (fixed several rounds ago), but not an equal-length in-place
  *overwrite* -- another writer with this inode's access can still swap
  validated content for a differently-shaped one, byte-for-byte
  length-equal, undetected by a size comparison. The only correct fix is
  binding validation and parsing to the same immutable bytes (e.g.
  reading the entire archive into a private buffer up front), which
  defeats this format's whole design goal of letting `read_manifest()`/
  `read_blob()` touch only the one member each actually needs -- a real,
  cross-cutting architecture change to this module's I/O model, not a
  follow-up to this preflight's own scope. Documented as a known residual
  in `reject_absurd_central_directory()`'s own docstring, alongside the
  growth case it already named; not fixed here.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `write_manifest()`'s existing chunk-by-chunk `iterencode()`
  size check (fixed several rounds ago) still couldn't reject a manifest
  containing a single oversized *string* value before materializing it --
  `json.JSONEncoder.iterencode()` yields one whole escaped string as a
  single chunk (confirmed empirically:
  `iterencode({'a': 'x'*2_000_000})` yields one 2000002-byte chunk), so
  the running byte count only ever sees that one large allocation *after*
  it already happened, defeating the whole point of the incremental check
  for exactly this input shape. Fixed with a new preflight,
  `oversized_raw_string()` (`abicheck/storage/bundle_archive_json_guard.py`,
  a new leaf module split out purely to keep both callers under the
  ADR-061 800-line production cap), that walks the *raw*, unescaped
  string leaves before `iterencode()` ever runs: JSON escaping only ever
  grows a string's encoded length, never shrinks it, so a raw string
  already longer than the limit is guaranteed to encode past it too, and
  this can be checked safely without materializing the larger escaped
  form. Applied identically in both `BundleArchiveWriter.write_manifest()`
  and `write_bundle_facts_archive()`'s own higher-level preflight (which
  had the identical gap). (2) `read_bundle_facts_archive()`'s
  `manifest_blob` fetch only ever charged `_cached_blob()`'s underlying
  raw bytes against the aggregate decoded-byte budget once, on a hash's
  first fetch -- correct for the bytes themselves, but the subsequent
  `json.loads()`/`manifest_from_dict()` call always builds a *fresh*
  object graph from them regardless of whether the fetch was a cache hit.
  When `manifest_blob` shares a content hash with an already-fetched
  `library_blobs` entry (a cache hit), this is the identical "duplicate
  materialization" the per-library-name loop already re-charges for its
  own deep-copied `AbiSnapshot` -- uncharged here, a single large shared
  blob could be parsed twice while billed once, bypassing the aggregate
  cap. Now charged the same way: a cache hit's own bytes are re-added to
  the running total (and rejected if that pushes past the cap) before the
  second `json.loads()`/`manifest_from_dict()` materialization runs.

  `abicheck/storage/bundle_facts_validation.py` (new): the two
  `_validated_alias_map`/`_validated_filename_map` helpers moved out of
  `bundle_facts.py` (unmodified except for their now-public names) purely
  to keep that module under the same 800-line cap after these fixes --
  `storage/` already owns this repo's persisted-field validators and has
  no coupling to either caller beyond the two functions themselves.
