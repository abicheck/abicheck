### Fixed

- **`abicheck.package.TarExtractor._safe_extract_zst_tar`**: the round-45
  fix bounding the external `zstd` CLI fallback's reader thread with an
  overall timeout used an unbounded `queue.Queue()` between the reader
  thread and the main thread's own decompression-bomb size check --
  letting the reader race arbitrarily far ahead and buffer gigabytes of
  already-decoded data before the size check ever ran, defeating the
  point of the limit. The queue is now bounded to a small, fixed number
  of chunks, so `put()` blocks (real pipe backpressure) once full.
- **`abicheck.product_baseline.pack_product_baseline`**: a library-shaped
  symlink whose real target is *not* itself independently library-shaped
  (e.g. `libfoo.so -> payload`, where `payload` is ordinary data) got no
  `LibraryEntry` at all -- unlike the ordinary case (a symlink aliasing
  another declared library, matched by unpack's identity-based check),
  there was no other declared identity for it to match against, so an
  honestly packed archive of exactly this shape could not round-trip:
  `unpack_product_baseline` still discovered the symlink as a library by
  name and rejected the archive as tampered. Packing now records a
  standalone entry for such a symlink; unpack's own manifest re-validation
  is checked against the symlink itself (not its fully-resolved target),
  matching the identity packing just declared.
