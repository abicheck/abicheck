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
