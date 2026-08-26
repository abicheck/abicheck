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
