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

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `write_bundle_facts_archive()`'s own mirrored aggregate-byte
  preflight (added in the previous round, to accept exactly what its
  paired reader would) only charged `manifest_blob`'s second-materialization
  bytes when its hash was *not* already among the library hashes -- the
  reader's own new charge (previous round, above) fires unconditionally
  whenever a manifest is present, including the *shared*-hash case, so the
  writer could still accept an archive its own reader would then reject.
  Reproduced: a 20-byte shared payload under a 30-byte cap wrote
  successfully (`decoded_size_bytes == 40`) but failed to reload. Fixed by
  dropping the now-wrong `not in hash_counts` condition -- the extra
  charge applies unconditionally whenever `manifest_blob` is present,
  matching the reader in both directions (a genuine raw-fetch charge on a
  cache miss, or the second-materialization charge on a cache hit). (2)
  `oversized_raw_string()`'s own raw-string preflight (added two rounds
  ago specifically to avoid materializing an oversized string's encoded
  form) still called `obj.encode("utf-8")` on the whole string before
  comparing its length -- for a value already guaranteed to be oversized
  (a multi-gigabyte string, say), that allocates a second object as large
  as the input, exactly the allocation the preflight exists to prevent.
  Fixed with a new `_utf8_length_exceeds()` helper: a Python `str`'s
  *character* count is always a lower bound on its UTF-8 *byte* count (1-4
  bytes per codepoint), so a character count alone already over the limit
  proves the byte count is too, with zero encoding; otherwise the
  remainder is encoded incrementally in bounded (64 Ki-character) chunks,
  stopping as soon as the running total exceeds the limit, so no single
  `encode()` call ever materializes more than a bounded slice regardless
  of the input's total size.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `write_bundle_facts_archive()`'s own per-library-snapshot
  encode still called `json.dumps()`+`.encode()` directly -- materializing
  a full copy of one oversized snapshot's serialization before the
  aggregate cap check (checked only *after*) ever got a chance to reject
  it, even though the loop's own cap was already checked *between*
  snapshots. Fixed by routing it through a new `bounded_encode_utf8()`
  helper (`abicheck/storage/bundle_archive_json_guard.py`), which streams
  against the *remaining* aggregate allowance: a single oversized string
  field is caught cheaply via the existing `oversized_raw_string()` fast
  path, and the aggregate size across many individually-bounded fields is
  caught by streaming `JSONEncoder.iterencode()` and stopping as soon as
  the running byte count crosses the limit -- never a `json.dumps()` call
  on the full object. (2) `sniff_bundle_archive_format()` still did its
  own, separate `Path.stat()` then a separate `open()` -- the exact
  two-inode TOCTOU window `open_regular_file_for_format_sniff()`'s own
  identical race had already been fixed for (a regular file swapped for a
  blocking FIFO between the two calls could hang the second `open()`
  indefinitely), just never carried over to this sibling function. Fixed
  by delegating to that same helper's single O_NONBLOCK-open-then-fstat()
  classification, closing the fd itself since this caller only needs the
  classification, not the fd.

- **G40 bundle archive: three more Codex review findings, all real, all
  fixed.** (1) `_utf8_length_exceeds()`'s chunked fallback still called
  `.encode("utf-8")` with strict error handling, which raises
  `UnicodeEncodeError` for a lone surrogate (e.g. from a POSIX filename
  captured through `os.fsdecode()`'s `surrogateescape` handling of
  non-UTF-8 bytes) -- even though `json.dumps()`'s own `ensure_ascii=True`
  escaping round-trips the identical value fine as a plain `\uXXXX`
  sequence, so a value the ordinary JSON writer handles correctly could
  crash `save_bundle_facts(..., format="archive")` before ever writing
  anything. Fixed by encoding with `errors="surrogatepass"` in the
  pre-check (the real `iterencode()`-based encode elsewhere never sees a
  raw surrogate, since JSON's own ASCII escaping already turns it into
  plain ASCII text first). (2) Both `write_manifest()`'s and
  `write_bundle_facts_archive()`'s own oversized-string error messages
  re-encoded the already-rejected string a second time
  (`len(oversized.encode('utf-8'))`) purely to report its size --
  recreating the exact allocation the preflight exists to avoid. Fixed by
  having `oversized_raw_string()` return the byte count it already
  computed while detecting the oversize (`tuple[str, int] | None` instead
  of `str | None`), which both callers now use directly instead of
  re-encoding; both messages now read "at least N bytes" (an honest lower
  bound, not a claimed exact total, since a bare `len(s) > limit` fast
  path never computes -- or needs -- the real one). (3)
  `write_bundle_facts_archive()`'s `InstantiationManifest` payload still
  called `json.dumps()`+`.encode()` directly -- the identical unbounded-
  materialization gap the per-snapshot fix (previous round) closed one
  level down, just never carried up to the manifest itself. Fixed by
  routing it through `bounded_encode_utf8()` too, streamed against the
  same remaining allowance.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) The raw-string size preflight (`oversized_raw_string()`/
  `_utf8_length_exceeds()`) checked a string's *raw* UTF-8 byte length,
  but JSON's own `ensure_ascii=True` escaping can inflate a string's size
  well past that -- a quote/backslash doubles, and a control character or
  lone surrogate becomes a six-character `\uXXXX` sequence (up to 6x its
  raw 1-byte form). A raw-length-only check could therefore pass a string
  whose *escaped* form still exceeded the limit, and `JSONEncoder.
  iterencode()` still emits that one string as a single, whole escaped
  chunk regardless -- reproducing the exact vulnerability this preflight
  exists to prevent, just via escaping inflation instead of raw size.
  Fixed by replacing the raw-UTF-8-byte check with a new
  `_escaped_length_exceeds()`, computed via `json.encoder.
  encode_basestring_ascii()` -- the exact function `json.dumps()`/
  `JSONEncoder(ensure_ascii=True)` use internally -- applied to bounded
  chunks, so this measures the real escaped form rather than
  approximating it. This also subsumes the prior round's `surrogatepass`
  fix: since the escaped output is always pure ASCII, no `.encode()` call
  (and therefore no `UnicodeEncodeError` on a lone surrogate) is needed
  at all. (2) `sniff_bundle_archive_format()`/`open_regular_file_for_
  format_sniff()` classified a source purely from its first 4 bytes, so a
  concatenated/self-extracting archive (arbitrary bytes before its zip
  data -- already handled correctly by `BundleArchiveReader.open()`/
  `reject_absurd_central_directory()`, whose own EOCD-tail-scan is robust
  to any prefix) was misclassified as `"json"`, making `load_bundle_
  facts()`'s documented default (`format="auto"`) fail on a path the
  identical call with `format="archive"` opened fine. Fixed with a new
  `looks_like_zip_from_tail()` (`abicheck/storage/bundle_archive_cd_
  guard.py`, reusing that module's own EOCD search window) as a fallback
  classification when the byte-0 check says `"json"` -- a cheap
  "is it worth trying" scan, not full validation; an archive that passes
  it can still be rejected by the real preflight/`zipfile.ZipFile` itself.

- **G40 bundle archive: three more Codex review findings, all real, all
  fixed.** (1) `looks_like_zip_from_tail()` (the previous round's own
  fallback classification fix) accepted a bare 4-byte `PK\x05\x06` match
  anywhere in the tail with no further structural check -- a valid,
  gzip-compressed `BundleFacts` JSON file can coincidentally contain that
  signature in its compressed tail (gzip's own header comment field, or
  just compressed-data bytes), misclassifying a perfectly good
  `format="json"` file as `"archive"` and failing the documented
  `format="auto"` default outright even though `format="json"` on the
  identical path succeeds. Reproduced with a real, decodable gzip stream
  carrying a crafted `FCOMMENT` header field embedding the signature.
  Fixed by additionally requiring the signature's own EOCD comment-length
  field to account exactly for every byte between it and the file's true
  end -- the same structural fact a real, unmodified EOCD always
  satisfies (none of this module's own writers ever emit a non-empty
  archive comment), ruling out all but a vanishingly rare coincidence.
  (2) `BundleArchiveReader.read_manifest()`'s `json.loads(raw)` call
  caught `UnicodeDecodeError`/`json.JSONDecodeError` but not
  `RecursionError` -- a small `manifest.json` nested a few thousand
  levels deep (`[[[...]]]`) blows Python's json decoder's own recursion
  budget, a distinct exception class, so it escaped as a raw traceback
  instead of this module's `SnapshotError` contract. Now caught
  separately and translated the same way. (3) `save_bundle_facts()`
  rejected *any* `compression=` value other than the literal string
  `"auto"` for `format="archive"`, including `"none"` -- even though
  `"none"` is semantically compatible with the archive format's own
  always-on per-blob zstd compression (no *outer* envelope layer applies
  either way, which is exactly what `"none"` already means): only
  `"gzip"`/`"zstd"` are genuinely incompatible outer-envelope requests.
  Now `"auto"`/`"none"` are both accepted as no-ops; `"gzip"`/`"zstd"`
  still reject.

- **G40 bundle archive: one more Codex review finding, real, fixed.**
  `read_manifest()`'s `json.loads(raw)` call caught `UnicodeDecodeError`/
  `json.JSONDecodeError` but not the bare `ValueError` Python 3.11+'s own
  integer-string-conversion digit limit (`sys.get_int_max_str_digits()`,
  4300 by default) raises for a manifest containing an oversized integer
  literal -- a different exception than `JSONDecodeError` (which is
  itself a `ValueError` subclass, but this failure isn't raised through
  it), so it escaped this module's `SnapshotError` contract. Now widened
  to catch `ValueError` directly, which subsumes `JSONDecodeError` too.

- **G40 bundle archive: one more Codex review finding, real, fixed.**
  The `format="auto"` sniff's structural-EOCD tail-scan fallback (the
  previous round's own fix) could still be fooled by a valid, independently
  decodable gzip-compressed `BundleFacts` JSON file: unlike the already-
  closed `FCOMMENT` header field, a gzip `FEXTRA` sub-field can embed a
  `PK\x05\x06` whose comment-length field is crafted to land exactly at the
  file's true end, satisfying the structural check without being a real
  EOCD. Fixed by never running the tail-scan fallback against a prefix that
  already matches a recognized gzip/zstd magic -- that magic alone already
  resolves the format unambiguously (the plain-JSON path transparently
  decompresses from it), so there's nothing for the fallback to add and
  every reason not to trust its tail for such a file.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `BundleArchiveReader._read_stored_member()`'s streaming
  read only caught `zipfile.BadZipFile` (the member's CRC-32 check) --
  a transient I/O failure partway through (e.g. `EIO` on a network
  filesystem) raises a raw `OSError` from `ZipExtFile.read()` instead,
  escaping this module's `SnapshotError` contract the same way every
  other failure here is translated. Now caught and translated too. (2)
  `bundle_facts.py`'s two blob-decode call sites (each library's own
  snapshot blob, and `manifest_blob`) called `json.loads()` directly
  with no error handling at all -- unlike the already-hardened
  `BundleArchiveReader.read_manifest()`, neither invalid JSON syntax nor
  a deeply nested payload (`RecursionError`, a distinct exception class
  from `JSONDecodeError`) was translated, so `load_bundle_facts()` could
  leak a raw exception for a hostile/malformed blob. Both call sites now
  route through a new, shared `_load_blob_json()` helper mirroring
  `read_manifest()`'s own translation.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `BundleArchiveReader.__init__()` constructed `zipfile.
  ZipFile` inside a `try` block that didn't catch `UnicodeDecodeError` --
  a central-directory filename marked UTF-8 (general-purpose flag bit
  11) but storing invalid UTF-8 bytes makes `ZipFile` raise that
  exception while building its own file list, escaping this module's
  `SnapshotError` contract. Now caught alongside the other constructor
  failure classes. (2) `_read_stored_member()` only proactively checked
  the "encrypted" flag bit (`0x1`) before opening a member -- flag bits
  5/6 (compressed-patched data, strong encryption) make `ZipFile.
  open()` raise a bare `NotImplementedError`, at lazy-open time rather
  than construction, so this also escaped untranslated. Now rejected
  proactively the same way the encrypted bit already is.

- **G40 bundle archive: one more Codex review finding, real and fixed.**
  `BundleArchiveReader.__init__()`'s widened exception handling (above)
  covers the *central directory*'s own filename decoding, done once at
  construction. But `zipfile.ZipFile.open()` separately re-reads and
  re-decodes the *local* file header's own filename -- a distinct copy
  the format allows to diverge from the central directory's -- whenever
  that local header's own general-purpose flag bit 11 is set, regardless
  of what the central directory recorded. A crafted local header can set
  that bit over invalid UTF-8 bytes, raising a bare `UnicodeDecodeError`
  neither `read_manifest()` nor `read_blob()` caught, since this happens
  lazily inside `_read_stored_member()`'s `self._zf.open(name)` call, not
  at construction. Now caught and translated to `SnapshotError` alongside
  the pre-existing `BadZipFile`/`OSError` handling there.

- **G40 bundle archive: two more real Codex review findings, both fixed
  -- one a silent-truncation guard, one a genuine FIFO-hang regression.**
  (1) `read_blob()`'s zstd `stream_reader()` pass can decompress a
  truncated frame with no error at all, silently yielding fewer bytes
  than intended instead of raising -- confirmed empirically: for a
  multi-block payload, truncating the compressed bytes by as little as
  one byte can decompress cleanly to a partial-but-plausible prefix, no
  exception. The post-decode content-hash check is not sufficient on its
  own: a hostile archive can name the member after the truncated
  payload's own (still-correct) hash, defeating it. Fixed by extracting
  `snapshot_io._decompress_zstd`'s own three-Codex-round-corrected
  frame-completeness cross-check (`.eof`/declared-content-size, walking
  every concatenated frame) into a new shared leaf,
  `storage/zstd_frame_guard.validate_zstd_frame_completeness`, now called
  from both `_decompress_zstd` and `read_blob()` -- sharing the
  already-hard-won logic rather than reimplementing (and re-risking) it,
  while keeping each call site's own window-size/decompression policy
  independent. (2) `open_regular_file_for_format_sniff()`'s non-regular-
  file branch opened the path (nonblocking), fstat'd it, then immediately
  closed it before returning `(None, "json")` -- for a FIFO with a
  one-shot external producer (opens for write, writes once, closes), this
  preliminary open()+close() can itself complete the producer's own
  blocking open()-for-write, letting it write and exit *before* the
  caller's real, separate read ever opens the FIFO -- which then blocks
  forever, since a FIFO read-open blocks until a writer connects, and the
  one-shot producer already came and went. Reproduced with a real FIFO +
  subprocess producer: pre-fix, the producer got `BrokenPipeError` and
  the follow-up real read hung indefinitely. Fixed by checking
  regularity via a path-level `stat()` *before* ever opening the path --
  `stat()` never blocks and never "connects" a reader the way `open()`
  does, so a FIFO is never touched at all until the one real read that
  will actually consume it; the existing fd-level `fstat()` (on what's
  actually opened) stays the source of truth against a concurrent swap
  in between, unaffected. Regression tests (both in
  `tests/test_bundle_archive_cd_guard.py`, moved there for line-budget
  headroom): `TestReadBlobRejectsTruncatedZstdFrames` (a real
  multi-block zstd frame truncated by 3 bytes, with a premise check
  confirming real `zstandard` really does silently short-decode it) and
  `TestSniffDoesNotConsumeAOneShotFifoProducer` (a real subprocess
  producer + explicit synchronization sleeps, run through a bounded
  `Thread.join()` so a regression reads as a clean assertion failure
  rather than hanging the test run itself). Both confirmed to fail
  against the pre-fix code -- the truncated-frame test with no exception
  raised, the FIFO test with the real read blocking past its 5s join
  timeout, reliably across repeated runs.

- **G40 bundle archive: one more Codex review finding on the just-shared
  `zstd_frame_guard.py`, real and fixed.** A member truncated to just the
  4-byte zstd magic (`28 b5 2f fd`, no complete frame header at all)
  decodes to `b""` with no error at all via `stream_reader()` -- the
  bounded primary pass in `read_blob()` therefore succeeds cleanly, and
  the new frame-completeness cross-check's own `get_frame_parameters()`
  call then raises on that same truncated input, but was swallowed by a
  blanket `except Exception: pass` (inherited verbatim from
  `snapshot_io._decompress_zstd`'s own pre-existing pattern) on the
  reasoning that "the primary pass already proved the stream decodes
  cleanly" -- which is exactly false here, since the primary pass was
  fooled by the identical truncation. A member named after the empty
  payload's own hash therefore passed the post-decode content-hash check
  too, so `BundleArchiveReader.read_blob()` silently returned `b""` for a
  genuinely malformed archive. Fixed by treating any per-frame header
  parse failure inside the validation loop as corruption -- the loop only
  ever calls `get_frame_parameters()`/`decompressobj()` on a non-empty
  `remaining` chunk (the `while` guard), so there is no legitimate reason
  for a parse to fail there. Regression test:
  `tests/test_bundle_archive_cd_guard.py::
  TestReadBlobRejectsTruncatedZstdFrames::
  test_read_blob_raises_for_a_member_truncated_inside_the_frame_header`
  (a premise check confirming real `zstandard` silently decodes the bare
  magic bytes to nothing, then a full `BundleArchiveReader.read_blob()`
  round trip); confirmed to fail against the pre-fix code with no
  exception raised.

- **G40 bundle archive: one more Codex review finding, real and fixed --
  the same swallowed-parse-failure class, one shape further.** A
  completely empty (zero-byte) stored blob member decodes to `b""` with
  no error via `stream_reader()`, and `validate_zstd_frame_completeness`'s
  own `while remaining:` loop never executes at all for empty `data` --
  "validating" with zero frames actually checked, rather than raising.
  A member named after the empty payload's own hash would then pass the
  post-decode content-hash check too, so `read_blob()` would silently
  accept an archive containing no zstd frame whatsoever as a valid empty
  blob. Confirmed `BundleArchiveWriter.put_blob()` always calls
  `ZstdCompressor.compress()` unconditionally, even for an empty payload
  -- producing a real, non-empty frame every time -- so a genuinely
  zero-byte member can never be this codebase's own legitimate output.
  Fixed by requiring `data` to be non-empty before entering the
  per-frame walk at all. Regression test:
  `tests/test_bundle_archive_cd_guard.py::
  TestReadBlobRejectsTruncatedZstdFrames::
  test_read_blob_raises_for_a_completely_empty_member` (a premise check
  confirming real `zstandard` silently decodes empty input to nothing,
  then a full `BundleArchiveReader.read_blob()` round trip); confirmed
  to fail against the pre-fix code with `read_blob()` returning `b""`
  instead of raising.

- **G40 bundle archive: one more Codex review finding, real and fixed --
  a false-positive rejection this time, not a missed one.** A standard
  zstd "skippable frame" (magic `0x184D2A50`-`0x184D2A5F`, an arbitrary-
  payload frame the format spec permits between real data frames) is
  correctly skipped by `stream_reader()`, but `validate_zstd_frame_
  completeness()`'s per-frame walk had no notion of it: `get_frame_
  parameters()`/`decompressobj()` don't recognize skippable frames
  either, misreading the frame's own 4-byte Frame_Size field as a bogus
  content-size declaration -- so a legitimate externally-produced
  multi-frame stream with an interspersed skippable frame was rejected
  as "corrupt or truncated" even though the real primary decompression
  pass decoded it correctly. Fixed by detecting the skippable-frame
  magic range and advancing past it (validating its own header isn't
  itself truncated, and that it doesn't claim more payload than is
  present) without treating it as a data frame. A second, self-caught
  round: skipping skippable frames blindly reopens a *different* hole --
  a *lone* skippable frame (no real data frame at all) would then pass
  with zero frames actually validated, structurally identical to the
  already-fixed completely-empty-member bypass. Fixed by requiring at
  least one real data frame to have been validated by the end of the
  walk (replacing the earlier, narrower "data must be non-empty"
  up-front check, which a leading skippable frame would have failed
  incorrectly). Regression tests:
  `tests/test_bundle_archive_cd_guard.py::TestReadBlobHandlesSkippableFrames`
  -- one confirming a real interspersed-skippable-frame stream now
  round-trips correctly through `BundleArchiveReader.read_blob()`
  (premise-checked against real `zstandard`, confirmed to fail against
  the pre-fix code with a false "corrupt or truncated" error), one
  confirming a stream made only of skippable frames still raises.

- **G40 bundle archive: one more Codex review finding, investigated in
  depth, documented rather than fixed with a code change.** For a single
  blob near `DEFAULT_MAX_BLOB_BYTES` (1 GiB), `read_blob()`'s bounded
  primary decompression pass and `validate_zstd_frame_completeness()`'s
  own per-frame re-decompression can each hold a full-size decoded
  buffer alive at once, so real peak transient memory approaches 2x the
  configured cap, not 1x, as the docstring previously implied.
  Investigated running validation *before* the bounded primary pass to
  avoid the overlap and rejected it: `python-zstandard`'s
  `decompressobj().decompress()` has no bound or `max_length` (confirmed
  empirically -- unlike `stream_reader()`'s chunked reads), so validating
  first means calling this unbounded API on a not-yet-proven-safe input,
  reintroducing the exact unbounded-decompression-bomb risk the current
  ordering exists to prevent -- a materially worse failure mode than a
  transient 2x memory ceiling. Closing this properly needs either a
  chunked/bounded per-frame decompression primitive this library version
  doesn't expose, or a larger redesign merging both passes into one --
  out of proportion to a P2 finding under continued review pressure, per
  this codebase's own "known gaps over risky reactive patches"
  convention. Documented the real peak-memory formula directly in
  `validate_zstd_frame_completeness()`'s own docstring so a caller sizing
  `max_decoded_bytes` budgets for roughly double that value, rather than
  leaving the discrepancy implicit.

- **G40 bundle archive: skippable-frame walk was quadratic in stored
  size (P1, real DoS).** `validate_zstd_frame_completeness()`'s
  skippable-frame loop advanced with `remaining = remaining[total:]` on
  a plain `bytes` object -- each slice copies the entire unread suffix,
  so a stream made of many tiny skippable frames ahead of one real data
  frame walks in O(n^2) rather than O(n). Confirmed empirically: 200,000
  skippable frames (~1.6 MiB of stored bytes, well within the archive
  reader's near-1-GiB stored-member cap) took ~11s to validate. Fixed by
  switching the walk to a zero-copy `memoryview` cursor --
  `struct.unpack_from()`, `zstandard.get_frame_parameters()`, and
  `decompressobj().decompress()` all accept a `memoryview` directly
  (confirmed empirically), so no frame's own processing pays a
  conversion cost either; `decompressobj().unused_data` still returns a
  fresh `bytes` copy of what follows a real data frame, immediately
  rewrapped in a `memoryview` so a real frame followed by more skippable
  frames doesn't reintroduce the same quadratic slicing. The same
  200,000-frame case now completes in ~0.1s. New regression test
  (`tests/test_bundle_archive_cd_guard.py::
  TestReadBlobHandlesSkippableFrames::
  test_read_blob_walks_many_skippable_frames_in_near_linear_time`,
  confirmed to fail against the pre-fix code at ~10.6s against a 5s
  bound) exercises this through the public `BundleArchiveReader.
  read_blob()` entry point at the same scale the finding was reported
  against, not just the shared helper in isolation.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) The real-data-frame path had its own, independent copy
  of the just-fixed skippable-frame quadratic shape: feeding a whole
  (potentially huge) memoryview to `decompressobj().decompress()` in
  one call per real frame makes `.unused_data` materialize a fresh
  copy of *everything* after the frame -- confirmed empirically at ~8s
  for 160,000 empty real data frames (~1.4 MiB), a real DoS vector on
  the same public `BundleArchiveReader.read_blob()`/`read_snapshot_
  bytes()` paths. Fixed by feeding each real frame incrementally in
  small, geometrically-growing chunks (starting at 256 bytes, doubling
  up to a 1 MiB cap) and stopping as soon as `decompressobj.eof`
  flips -- `python-zstandard` supports feeding one frame across
  multiple `decompress()` calls on the same `decompressobj()`
  (confirmed empirically), so `.unused_data`'s copy cost is bounded by
  the last chunk fed rather than the whole remaining stream. The same
  160,000-frame case now completes in ~0.5s; a single large
  low-compressibility frame (20 MiB) still validates in ~9ms since the
  chunk size grows geometrically to the 1 MiB cap. New regression test
  (`test_read_blob_walks_many_small_real_frames_in_near_linear_time`,
  confirmed to fail against the pre-fix code at ~8.2s against a 5s
  bound) exercises this through the public `read_blob()` entry point
  at the reported scale. (2) `read_bundle_facts_archive`'s manifest
  `schema_version`/`bundle_facts_schema_version` fields were coerced
  via a bare `int()` call, which silently truncates `1.9` to `1`,
  accepts `True`/`False` as `1`/`0` (bool is an int subclass), parses
  the JSON string `"1"` as if it were a real integer, and leaks a raw
  `TypeError` for `None` instead of this module's own `SnapshotError`
  contract -- a malformed or hostile manifest could read as a
  supported schema version, or crash with the wrong exception type,
  instead of failing closed. Fixed with a new
  `_require_int_schema_version()` helper that rejects anything that
  isn't a real, non-bool `int` before it ever reaches the version
  comparison. New parametrized regression test
  (`test_load_rejects_a_non_integer_schema_version`, 8 cases across
  both fields, all confirmed to fail against the pre-fix code with
  either a raw `TypeError` or a silent, wrong-typed pass-through)
  exercises this through the public `load_bundle_facts()` entry point.

- **G40 bundle archive: malformed nested snapshot shapes leaked a raw
  exception instead of this module's own error contract (Codex review,
  P2).** A content-addressed library blob that decodes to a valid JSON
  *object* (passing the existing shape check) but has a malformed
  nested shape -- e.g. `{"functions": [None]}` -- made
  `snapshot_from_dict()` raise a raw `TypeError`/`KeyError`/
  `AttributeError`/`IndexError` straight out of
  `read_bundle_facts_archive()`, so the public
  `load_bundle_facts(..., format="archive")` path leaked a traceback
  for a corrupt archive instead of reporting it through this module's
  `ValueError`/`SnapshotError` vocabulary. Confirmed empirically across
  a range of malformed shapes (`{"functions": [None]}` ->
  `TypeError`, `{"elf": "notadict"}` -> `KeyError`, a bare `None`/`str`/
  `int` top-level value -> `AttributeError`). Fixed with a new
  `_load_snapshot_dict()` wrapper (mirroring `_load_blob_json`'s own
  existing translation pattern in this same function) that catches
  `TypeError`/`KeyError`/`AttributeError`/`IndexError` and re-raises as
  `SnapshotError` with the offending library named. New regression test
  (`test_load_translates_a_malformed_nested_snapshot_shape`, confirmed
  to fail against the pre-fix code with a raw `TypeError`) exercises
  this through the public `load_bundle_facts()` entry point.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `read_bundle_facts_archive()`'s `copy.deepcopy(cached_
  snapshot)` call (the fast path for a second library name sharing an
  already-parsed blob's hash) had no exception translation: a
  sufficiently deep value (e.g. ~900 nested lists under `constants`)
  can blow `deepcopy`'s own pure-Python recursion budget even though
  `snapshot_from_dict()` -- backed by a C-accelerated JSON decoder that
  tolerates much deeper nesting -- already succeeded for the *first*
  name referencing that hash, leaking a raw `RecursionError` for the
  *second*. Fixed by wrapping the `deepcopy()` call the same way the
  earlier `_load_snapshot_dict()` fix wraps `snapshot_from_dict()`
  itself. (2) `schema_version`/`bundle_facts_schema_version` only
  checked their *upper* bound (rejecting a version newer than this
  abicheck supports) -- `0` or a negative integer, which never existed
  as a real schema version, silently passed through and was parsed as
  if it were v1's layout. Fixed by requiring both fields to fall within
  `1..MAX` (collapsing the previous upper-bound-only check and a
  planned separate lower-bound check into one combined range check, to
  stay within the ADR-061 line cap). New regression tests for both
  (`test_load_translates_a_recursion_error_when_cloning_a_shared_
  snapshot`, `test_load_rejects_a_schema_version_that_never_existed`),
  all confirmed to fail against the pre-fix code (a raw `RecursionError`
  for the first, a silent pass-through for the second) before applying
  the fix.

- **G40 bundle archive: two more Codex review findings, both real, both
  fixed.** (1) `json.loads()` has no cap on the *number* of container
  nodes it materializes -- only decoded *byte* size was bounded, so a
  highly compressible payload of many small objects under a key
  `snapshot_from_dict()` ignores could inflate real memory far past its
  own byte size (confirmed empirically: ~150MB RSS from a 6MB payload
  of ~2M empty objects, well within every existing byte cap). Fixed by
  feeding `_load_blob_json()`'s `json.loads()` call an `object_pairs_
  hook` that counts every JSON object node (confirmed to fire for
  objects nested inside arrays too) and raises once a
  `DEFAULT_MAX_JSON_OBJECT_NODES` (1,000,000) budget is exceeded --
  confirmed empirically to abort decoding immediately, with no further
  materialization, once the budget trips. (2) `schema_version`/
  `bundle_facts_schema_version` were read via `manifest.get(key,
  default)`, silently defaulting to 1 whenever either key was absent --
  since no pre-v1 archive layout ever existed, an unrelated or
  incomplete manifest containing only `library_blobs` could masquerade
  as the current format. Fixed by requiring both keys present,
  raising `IncompatibleSnapshotSchemaError` otherwise, consolidated
  into one loop over both fields to fit the ADR-061 line cap. This
  required updating ~15 existing test fixtures across
  `test_bundle_facts_archive.py`/`test_bundle_facts_archive_hardening.py`
  that incidentally omitted these keys (their own point was testing an
  unrelated failure mode -- missing `library_blobs`, a malformed blob
  shape, etc. -- and would otherwise now fail for the wrong reason).
  New regression tests for both
  (`test_load_bounds_object_allocation_during_blob_decoding`,
  `test_load_rejects_a_manifest_missing_a_schema_version_key`), all
  confirmed to fail against the pre-fix code before applying the fix.

- **G40 bundle archive: three more Codex review findings, all real, all
  fixed, plus a shared primitive to close both together.** (1) The
  object-count budget above only ever fires for JSON *object* nodes --
  `object_pairs_hook` has no array counterpart, so a payload of many
  empty `[]` nodes under an ignored key sailed through untouched
  (confirmed empirically: a 100,000-array payload still loaded under a
  budget sized just above a real snapshot's own mapping count). (2)
  `BundleArchiveReader.read_manifest()` never enforced any container-node
  budget at all -- a sub-64 MiB `manifest.json` could still hold millions
  of container nodes under a field its own schema checks never look at,
  materializing a multi-gigabyte object graph before validation. (3) A
  zstd stream produced by an external tool may legitimately start with a
  standard skippable frame (e.g. metadata) ahead of the real data frame --
  `validate_zstd_frame_completeness` already handles this correctly once
  reached, but `read_snapshot_bytes()`'s own compression *detection* only
  ever looked at a bare 4-byte prefix, classified the leading skippable
  magic as uncompressed, and raised a suffix/magic mismatch instead of
  ever reaching decompression.

  Findings (1) and (2) share one root cause and one fix: a new
  `abicheck.storage.json_budget` module (`storage/` rather than
  `bundle_facts.py`, since ADR-061 forbids `storage/` importing the
  latter) provides a single linear pre-scan, `check_json_container_
  budget()`, that counts every `{`/`[` container-node start outside a
  string literal -- skipping whole string tokens so a bracket inside a
  string value is never miscounted -- and raises before `json.loads()`
  ever runs. Considered and rejected: forcing `json`'s pure-Python
  scanner via a `JSONDecoder.parse_array` override (the only way to reach
  a genuine array-parsing hook) measured ~3.7x slower on an ordinary 2 MB
  snapshot blob, a real cost on every legitimate load, not just an
  adversarial one -- the pre-scan pays no such tax. `bundle_facts.py`'s
  `_load_blob_json` and `bundle_archive.py`'s `read_manifest()` both now
  call this one primitive instead of `_load_blob_json`'s previous,
  object-only `_counting_hook`.

  Finding (3) is fixed in the same shared module the frame-completeness
  validator already lives in: a new `skip_leading_skippable_frames()` in
  `storage/zstd_frame_guard.py` (reusing that module's existing
  skippable-frame magic/size-field constants) is now consulted by
  `detect_compression_from_bytes()` before checking gzip/zstd magic --
  a no-op for a bare 4-byte prefix too short to contain a full skippable
  frame, so every existing 4-byte-prefix-only caller is unaffected.
  `read_snapshot_bytes()`'s second classification call now passes its
  already-fully-buffered `raw` (not just `raw[:4]`) so it can actually
  see past an arbitrarily-sized leading skippable frame.

  **A fourth, independent finding surfaced while adding regression tests
  for the above: Python 3.14 no longer raises `RecursionError` for a
  pathologically deep `[[[...]]]` payload at all** (confirmed empirically:
  10,000 levels of array nesting parses cleanly into a `list` on 3.14,
  where every earlier Python version raised). Both `_load_blob_json`'s and
  `read_manifest()`'s existing `except RecursionError` translations
  silently stopped firing on that version, letting a deeply nested blob/
  manifest reach downstream code expecting a `dict` and fail with an
  untranslated, unrelated `ValueError` instead of this module's own
  `SnapshotError` contract -- caught by CI's `unit-tests (ubuntu-latest,
  3.14, ...)` lane on three existing tests. Fixed by having the same
  `check_json_container_budget()` pre-scan also track nesting depth
  (independent of node count) and raise a new `JsonNestingTooDeepError`
  once a `DEFAULT_MAX_JSON_NESTING_DEPTH` (2,000 -- comfortably above the
  900-level depth an existing, deliberately-boundary-cased test relies on
  `json.loads()` itself still succeeding at, and comfortably below any
  realistic hostile payload) is exceeded, translated to the identical
  "too deeply nested" `SnapshotError` both call sites already raised --
  now the primitive actually enforcing it, with the pre-existing
  `except RecursionError` kept only as a fallback net. New primitive-level
  tests in `tests/test_json_budget.py` pin the Python-3.14 regression
  directly, independent of either call site.

  A fifth, unrelated CI failure on the same commit -- `windows-latest`
  only -- was also root-caused and fixed: `TestBundleArchiveWriterAtomicity
  ::test_close_failure_removes_temp_file_even_when_wrapper_close_also_
  fails`'s fault-injection double for a failing `close()` fully replaced
  the method without calling the real implementation, so the real OS
  file handle was never actually released. On POSIX that's harmless (an
  unlinked-but-open file keeps its inode alive), but on Windows the
  test's own subsequent cleanup `unlink()` then failed with a genuine
  `WinError 32` ("used by another process"), masking the simulated
  failure the test means to assert on. Confirmed empirically that a real
  Python file object releases its underlying fd even when `close()`
  itself raises (e.g. from a failing `flush()`) -- so this was a test
  fault-injection gap, not a production close-ordering bug. Fixed by
  having the test double call the real `close()` first, then raise,
  matching real close-failure semantics and letting the simulated error
  surface deterministically on every platform.

  New tests: `tests/test_bundle_facts_archive_hardening.py`'s
  `test_load_bounds_array_allocation_during_blob_decoding`/
  `test_load_bounds_container_allocation_while_decoding_the_manifest`;
  `tests/test_snapshot_compression_skippable_frames.py` (new file, split
  out of `test_snapshot_compression.py` to stay under its own ADR-061
  no-growth baseline) for the leading-skippable-frame detection fix;
  `tests/test_json_budget.py` (new file) for the shared primitive itself,
  including the Python-3.14 depth regression. All confirmed to fail
  against the pre-fix code before applying each fix.

- **G40 bundle archive: three direct follow-up Codex review findings on the
  skippable-frame fix above, all real, all fixed.** (1) `read_snapshot_
  bytes()`'s own internal classification call was fixed to see past a
  leading zstd skippable frame, but the *other* public probes --
  `detect_snapshot_compression()`, `read_snapshot_storage_info()`, and
  `bounded_decoded_prefix()` -- each still only read a bare 4-byte prefix
  from disk, so a real zstd file with a leading skippable frame still
  misclassified as uncompressed through those entry points (worse,
  `bounded_decoded_prefix()` returned the still-compressed raw bytes as
  though they were the decoded content). (2) The new `skip_leading_
  skippable_frames()` helper (added for the sibling fix above) reintroduced
  the exact quadratic-slicing bug `validate_zstd_frame_completeness` had
  already been fixed for in an earlier round -- a bare `remaining =
  remaining[total:]` on `bytes` copies the entire unread tail every
  iteration, confirmed at ~11s for 200,000 zero-length skippable frames,
  and `read_snapshot_bytes()` now passes its whole buffer through this
  same helper. (3) `bundle_archive.py`'s own archive-format sniff
  (`open_regular_file_for_format_sniff()`) only ever read a bare 4-byte
  prefix too, so a leading-skippable-frame-prefixed zstd `BundleFacts`
  JSON envelope fell through to the ZIP-tail EOCD heuristic unnecessarily
  -- and, since zstd permits a skippable frame anywhere in a stream
  (including *after* the real data frame), a crafted trailing skippable
  frame whose own user data ends in a structurally-plausible empty-ZIP
  EOCD landing exactly at file end let a real, independently-decodable
  zstd JSON blob misclassify as `"archive"` (reproduced with exactly this
  construction, not a hypothetical one).

  Fixed (2) the same way the earlier `validate_zstd_frame_completeness`
  fix did: an integer cursor into the original `bytes`, sliced exactly
  once at the end. Fixed (1) and (3) with two new shared primitives in
  `storage/zstd_frame_guard.py` -- `starts_with_skippable_frame_magic()`
  (a fast, no-I/O check so the overwhelmingly common non-skippable-frame
  case never pays for anything extra) and `read_past_leading_skippable_
  frames()` (an escalating, forward-only, capped read that grows a
  caller's initial small prefix only when ambiguous) -- consumed by
  `snapshot_io.py`'s three probes (which also needed the *original*,
  unstripped bytes preserved for hashing/plain-content use, unlike the
  first fix's classification-only need) and by `bundle_archive.py`'s own
  sniff (which additionally re-applies `skip_leading_skippable_frames()`
  to the escalated read before the magic-prefix check, since the shared
  primitive intentionally returns raw bytes, not the stripped view).

  New tests: `tests/test_snapshot_compression_skippable_frames.py` gained
  four more cases (the three public probes together, an escalation-is-
  skipped-for-ordinary-files guard, the primitive-level quadratic-time
  regression, and the public reader at the same 200,000-frame scale);
  `tests/test_bundle_archive_skippable_frame_sniff.py` (new file, same
  ADR-061 no-growth reasoning as the sibling split) covers the archive
  sniff directly, including the crafted-trailing-EOCD construction. All
  confirmed to fail against the pre-fix code before applying each fix.

- **Separately investigated, at the coordinator's request: a reported CI
  failure on the `unit-tests (ubuntu-latest, 3.13, false)` lane
  (`docs/reference/cli-reference.md` drift on `scan`'s `--allow-ast-
  frontend-fallback`/`--allow-unsupported-castxml` "Default" column).
  Confirmed pre-existing on `main`, unrelated to this PR, no fix applied
  here.** `docs/reference/cli-reference.md` is byte-identical between this
  branch and `main` at every point checked, and `scripts/gen_cli_
  reference.py --check` passes cleanly against both under the `click`
  version already installed in this environment (8.4.2). Reproduced by
  installing `click==8.5.0` (the newest PyPI release, and this repo's
  `pyproject.toml` pins only `click>=8.0`, a floor with no ceiling): the
  live-rendered reference then disagrees with the checked-in doc on
  exactly the two reported flags, on both this branch and a fresh clone of
  `main` alike. Root cause: Click 8.5.0 changed `is_flag=True` options to
  keep their `default` as an internal `UNSET` sentinel until lazily
  resolved (`Option.__init__`'s own new docstring/comment), rather than
  eagerly assigning `False` the way every older Click version did;
  `gen_cli_reference.py`'s `_default_str()` reads `param.default` directly
  or (via a fragile `type(default).__name__ == "Sentinel"` string check)
  and cannot tell a still-unresolved flag default from a genuinely
  optionless one, rendering `—` instead of `` `False` ``. A real, if
  narrow and unrelated, bug in a doc-generation script combined with an
  unpinned dependency floor -- not something this PR's diff (`bundle_
  facts.py`/`storage/*`/`snapshot_io.py`) has any bearing on, and not
  fixed here per this repo's CI-red convention (pre-existing on the base
  branch).

- **A fourth, third-order follow-up on the same skippable-frame saga:
  `read_snapshot_bytes()`'s own *cap-selection* probe -- the code that
  decides whether the decoded-size limit (`max_decoded_bytes`) or the
  independent, much larger stored-size ceiling (`_max_stored_bytes()`)
  applies -- still only read a bare 4-byte prefix, one step ahead of the
  three probes already fixed above (Codex review, fresh evidence).** A
  zstd file starting with a leading skippable frame therefore had
  `compression_hint` stay `NONE` at that point (the same "4 bytes can't
  see past a skippable-frame header" gap the other three fixes closed),
  so the *stored*-size cap was wrongly applied instead of the decoded-size
  one -- even though the decisive, full-buffer classification a few lines
  later (`compression = detect_compression_from_bytes(raw)`) already sees
  the real zstd frame correctly. Reproduced exactly as reported: a
  219-byte stream (a 200-byte skippable frame ahead of a real zstd frame
  decoding to `{}`) read with `max_decoded_bytes=100` was rejected against
  the 100-byte *stored*-size cap, even though only 2 bytes are actually
  decoded. Fixed by escalating this probe past a leading skippable frame
  too, via the same shared `starts_with_skippable_frame_magic()`/
  `read_past_leading_skippable_frames()` primitives the other three call
  sites already use -- the escalated prefix can never exceed the file's
  real stored size (it's read forward from the same fd), so the
  pre-existing `stored_size > cap` check just below still catches an
  oversized file even when this escalation itself reads well past a small
  `cap`.

  New test:
  `test_read_snapshot_bytes_cap_selection_sees_past_leading_skippable_frame`
  in `tests/test_snapshot_compression_skippable_frames.py`, matching the
  finding's own repro shape exactly. Confirmed to fail against the
  pre-fix code before applying the fix.

- **A fifth, fourth-order follow-up on the same skippable-frame saga: the
  previous fix's own bounded escalation ceiling has an edge case (Codex
  review, fresh evidence).** The fourth-round fix above escalates
  `read_snapshot_bytes()`'s cap-selection probe past a leading skippable
  frame, but that escalation is deliberately bounded
  (`_BOUNDED_PREFIX_MAX_RAW_BYTES`, 1 MiB) so an adversarial file with an
  enormous/unbounded run of leading skippable frames can't force an
  unbounded read just to classify it -- correct, and unchanged here. The
  gap: when *legitimate* leading skippable-frame metadata happens to
  exceed that 1 MiB bound, the escalated read hits its ceiling without
  ever reaching the real data frame's own magic, and `compression_hint`
  fell all the way back to `NONE` -- applying the small *decoded*-size cap
  to the file's real, much larger *stored* size. Reproduced exactly as
  reported: a 2 MiB skippable frame ahead of a real zstd frame decoding to
  `{}` (2 bytes), read with `max_decoded_bytes=100`, was rejected against
  the 100-byte stored-size cap. Fixed by recognizing that the bare leading
  4-byte magic alone (`starts_with_skippable_frame_magic()`, cheap and
  no-I/O) already proves the file is zstd-family, independent of whether
  the bounded escalation manages to resolve the exact frame structure --
  decoupling "is this compressed, for cap selection" (answerable
  unconditionally from the leading magic) from "what exact frame structure
  does it have" (which may legitimately not resolve within the bounded
  probe). Only the cap picked at this one call site changes; the 1 MiB
  escalation ceiling itself is untouched, and the decisive full-buffer
  classification (`compression`) and the actual decompression/frame-
  completeness validation further down still run exactly as before, so a
  malformed/hostile file that merely *starts* with skippable-frame magic
  is still fully validated rather than trusted outright.

  New test:
  `test_read_snapshot_bytes_cap_selection_survives_escalation_ceiling` in
  `tests/test_snapshot_compression_skippable_frames.py`, matching the
  finding's own repro shape (a 2 MiB skippable frame, past the 1 MiB
  escalation ceiling). Confirmed to fail against the pre-fix code with the
  same wrong-cap `SnapshotError` before applying the fix.

- **Two more Codex review findings on this same PR, both real, both
  fixed -- a sixth-order follow-up in the skippable-frame area, plus an
  unrelated JSON-allocation-budget gap.** (1) The prior round's
  `read_snapshot_bytes()` cap-selection fix was the *fourth* of this
  module's four skippable-frame-aware probes to need this exact
  escalation-ceiling fallback, but the other three --
  `detect_snapshot_compression()`, `read_snapshot_storage_info()`, and
  `bounded_decoded_prefix()` -- still independently fell back to `NONE`
  (or, for `bounded_decoded_prefix()`, returned the still-compressed raw
  metadata bytes as though they were decoded content) whenever their own
  bounded escalation hit the same 1 MiB ceiling without reaching the real
  data frame. Reproduced exactly as reported: a 2 MiB skippable frame
  ahead of a real zstd frame made `detect_snapshot_compression()`/`read_
  snapshot_storage_info()` report `NONE` and `bounded_decoded_prefix()`
  return the raw skippable-frame bytes, even though `read_snapshot_
  bytes()` on the identical file decodes correctly. Fixed by extracting
  the fallback into one shared `_classify_with_skippable_fallback()` --
  the leading skippable-frame magic alone already proves the zstd family,
  independent of whether the bounded escalation can resolve the exact
  frame structure -- now used by all four probes (including `read_
  snapshot_bytes()`'s own cap-selection probe, refactored onto the same
  helper rather than keeping its own separate `known_compressed` special
  case). New test: `test_probe_call_sites_stay_correct_past_the_
  escalation_ceiling` in `tests/test_snapshot_compression_skippable_
  frames.py`, confirmed to fail against the pre-fix code on all three
  probes. (2) `storage/json_budget.py`'s container-node budget (closing
  an earlier round's object/array-only gap) still only counted container
  *starts* -- a matched string token was consumed and silently discarded
  rather than counted, and numbers/`true`/`false`/`null` were never
  matched by the scanner's regex at all. `json.loads()` allocates one
  real Python object per scalar value regardless of container shape, so
  a highly compressible payload of many scalar strings under an ignored
  field could still inflate real memory well past this budget's intent
  while passing the check cleanly -- reproduced exactly as reported:
  1,100,001 eight-character strings in a 12.1 MB payload passed the
  check while increasing RSS by ~76 MB. Fixed by widening the token
  regex to also match numbers and the three literal scalars, and
  counting every matched string/number/literal token (not just container
  opens) toward the same budget -- close tokens still only adjust nesting
  depth, so this doesn't affect the existing depth check. Two pre-existing
  tests (`test_a_bracket_inside_a_string_value_is_not_counted`, `test_an_
  escaped_quote_inside_a_string_does_not_desync_token_boundaries`) had
  their `max_container_nodes` values updated to the correct token counts
  under the new, intentionally-widened semantics -- their own point (no
  desync from a bracket/escaped-quote inside a string) is unaffected and
  still asserted. New tests: `test_counts_scalar_string_values_too`,
  `test_counts_number_and_literal_scalars_too` in `tests/test_json_
  budget.py`, both confirmed to fail against the pre-fix code.

  A third CI-visible signal on this same head commit was investigated
  and is unrelated to this PR: `test_generated_reference_is_in_sync_
  with_cli` (macos-latest/3.13, ubuntu-latest/3.14, and the `mutmut`
  baseline run that depends on a clean test pass) fails on a pre-existing
  Click 8.5.0 doc-drift for `scan`'s `--allow-ast-frontend-fallback`/
  `--allow-unsupported-castxml` options (`docs/reference/cli-reference.md`
  records `False`, a freshly-generated reference on these lanes reports
  `—`) -- a base-branch/environmental issue orthogonal to the skippable-
  frame or JSON-budget code this round touches, not fixed here.
